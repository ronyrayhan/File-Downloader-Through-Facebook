import os
import json
import uuid
import requests
import threading
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import zlib
import tempfile
import time
from facebook_service import FacebookService
import requests
import re
from config import *

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configuration
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

facebook_service = FacebookService(PAGE_ACCESS_TOKEN)

# Global operation tracking
operations = {}

class FileEncryptor:
    def __init__(self, chunk_size=10 * 1024 * 1024):  # 10MB chunks
        self.chunk_size = chunk_size
        self.file_signature = b'ENCRYPTED_FILE_v1.0'
    
    def derive_key(self, password, salt):
        """Derive encryption key from password using PBKDF2"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))
    
    def encrypt_file(self, input_file, output_base, password):
        """Encrypt file and split into PDF-like segments"""
        try:
            # Generate random salt
            salt = os.urandom(16)
            
            # Derive encryption key
            key = self.derive_key(password, salt)
            fernet = Fernet(key)
            
            # Read and compress original file
            with open(input_file, 'rb') as f:
                original_data = f.read()
            
            compressed_data = zlib.compress(original_data)
            
            # Encrypt the compressed data
            encrypted_data = fernet.encrypt(compressed_data)
            
            # Create final data with signature, salt, and encrypted data
            final_data = self.file_signature + salt + encrypted_data
            
            # Split into chunks
            total_chunks = (len(final_data) + self.chunk_size - 1) // self.chunk_size
            
            output_files = []
            for i in range(total_chunks):
                start = i * self.chunk_size
                end = min((i + 1) * self.chunk_size, len(final_data))
                chunk_data = final_data[start:end]
                
                # Create PDF-like output (base64 encoded)
                pdf_like_data = base64.b64encode(chunk_data)
                
                # Create output filename with consistent pattern
                output_file = f"{output_base}_part{i+1:03d}.pdf"
                output_files.append(output_file)
                
                # Write as PDF-like file
                with open(output_file, 'wb') as f:
                    f.write(b'%PDF-1.4\n')
                    f.write(b'%%\xE2\xE3\xCF\xD3\n')
                    f.write(b'1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n')
                    f.write(b'2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n')
                    f.write(b'3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Contents 4 0 R\n>>\nendobj\n')
                    f.write(b'4 0 obj\n<<\n/Length ' + str(len(pdf_like_data)).encode() + b'\n>>\nstream\n')
                    f.write(pdf_like_data)
                    f.write(b'\nendstream\nendobj\n')
                    f.write(b'xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000254 00000 n \n')
                    f.write(b'trailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n' + str(len(pdf_like_data) + 300).encode() + b'\n%%EOF\n')
                
                print(f"Created encrypted segment: {output_file} ({len(chunk_data)} bytes)")
            
            print(f"Encryption complete! Created {total_chunks} segment(s).")
            return output_files
            
        except Exception as e:
            print(f"Encryption error: {e}")
            return None

# Initialize encryptor
file_encryptor = FileEncryptor()

@app.route('/start_download', methods=['POST'])
def start_download():
    """Start download operation from a URL"""
    data = request.json
    file_url = data.get('file_url', '')
    
    if not file_url:
        return jsonify({'error': 'File URL is required'}), 400
    
    # Create operation ID (this will be our batch ID)
    batch_id = str(uuid.uuid4())
    
    # Store operation
    operations[batch_id] = {
        'status': 'downloading',
        'progress': 0,
        'current_stage': 'downloading',
        'file_url': file_url,
        'encrypted_files': [],
        'attachment_ids': [],
        'start_time': time.time()
    }
    
    # Start operation in background thread
    thread = threading.Thread(
        target=process_download_thread,
        args=(batch_id, file_url)
    )
    thread.daemon = True
    thread.start()
    
    return jsonify({'status': 'started', 'batch_id': batch_id})

def process_download_thread(batch_id, file_url):
    """Wrapper function to process download in background thread with app context"""
    with app.app_context():
        process_download(batch_id, file_url)

def process_download(batch_id, file_url):
    """Process download operation with encryption and Facebook upload"""
    try:
        operation = operations[batch_id]
        
        # Stage 1: Download the file from the provided URL
        operation['current_stage'] = 'downloading'
        operation['status'] = 'downloading'
        operation['progress'] = 10
        
        print(f"Downloading file from: {file_url}")
        
        # Download the file
        response = requests.get(file_url, stream=True, timeout=30)
        if response.status_code != 200:
            raise Exception(f"Failed to download file: HTTP {response.status_code}")
        
        # Save the downloaded file
        original_filename = secure_filename(file_url.split('/')[-1]) or "downloaded_file"
        local_file_path = os.path.join(UPLOAD_FOLDER, f"temp_{batch_id}_{original_filename}")
        
        with open(local_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        print(f"Download complete: {local_file_path}")
        operation['progress'] = 30
        
        # Stage 2: Encrypt the file
        operation['current_stage'] = 'encrypting'
        operation['status'] = 'encrypting'
        
        # Use consistent filename pattern (this is the key change)
        output_base = os.path.join(UPLOAD_FOLDER, f"enc_{batch_id}")
        encrypted_files = file_encryptor.encrypt_file(local_file_path, output_base, FIXED_PASSWORD)
        
        if not encrypted_files:
            raise Exception("File encryption failed")
        
        operation['encrypted_files'] = encrypted_files
        operation['original_filename'] = original_filename
        operation['progress'] = 50
        
        # Clean up original file
        os.remove(local_file_path)
        
        # Stage 3: Upload to Facebook and store attachment IDs
        operation['current_stage'] = 'uploading'
        operation['status'] = 'uploading'
        
        # Upload encrypted files and collect attachment IDs
        upload_results = facebook_service.upload_multiple_files(encrypted_files)
        
        # Store attachment IDs for client retrieval
        attachment_ids = []
        for result in upload_results:
            if 'attachment_id' in result:
                attachment_ids.append(result['attachment_id'])
        
        operation['attachment_ids'] = attachment_ids
        operation['progress'] = 70
        
        # Count successful uploads
        successful_uploads = len(attachment_ids)
        
        # Stage 4: Send messages with batch ID and attachment IDs
        operation['current_stage'] = 'sending'
        operation['status'] = 'sending'
        
        send_results = []
        for i, attachment_id in enumerate(attachment_ids):
            # Send the file with both batch ID and attachment ID in the message
            message_text = f"Batch: {batch_id}, Attachment: {attachment_id}, Part: {i+1}"
            send_result = facebook_service.send_attachment_with_message(
                RECIPIENT_ID,
                attachment_id, 
                'file',
                message_text
            )
            send_results.append(send_result)
            
            # Small delay to avoid rate limiting
            time.sleep(1)
        
        # Count successful sends
        successful_sends = sum(1 for result in send_results if 'error' not in result)
        operation['progress'] = 90
        
        # Clean up encrypted files
        for encrypted_file in encrypted_files:
            try:
                os.remove(encrypted_file)
            except:
                pass
        
        if successful_sends == 0:
            error_messages = [r.get('error', 'Unknown error') for r in send_results if 'error' in r]
            raise Exception(f"All file sends failed. Errors: {', '.join(error_messages[:3])}")
        
        operation['status'] = 'completed'
        operation['progress'] = 100
        
        print(f"Operation {batch_id} completed successfully. Sent {successful_sends} files.")
        
    except Exception as e:
        operation['status'] = 'error'
        operation['error'] = str(e)
        print(f"Operation {batch_id} failed: {e}")

@app.route('/operation_status/<batch_id>')
def operation_status(batch_id):
    """Get the status of an operation"""
    operation = operations.get(batch_id)
    if operation:
        return jsonify({
            'status': operation.get('status', 'unknown'),
            'progress': operation.get('progress', 0),
            'current_stage': operation.get('current_stage', ''),
            'encrypted_files': operation.get('encrypted_files', []),
            'original_filename': operation.get('original_filename', ''),
            'attachment_ids': operation.get('attachment_ids', []),
            'start_time': operation.get('start_time', 0)
        })
    else:
        return jsonify({'error': 'Operation not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=9999)
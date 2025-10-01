import os
import json
import requests
import time
import glob
import base64
import zlib
import re
from typing import Dict, List, Any, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from config import *

# Configuration
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

class FileDecryptor:
    def __init__(self):
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
    
    def decrypt_file(self, input_pattern, output_file, password):
        """Decrypt segmented files back to original"""
        try:
            # Find all segment files
            segment_files = sorted(glob.glob(input_pattern))
            
            if not segment_files:
                print("No segment files found!")
                return False
            
            # Read all segments
            combined_data = b''
            for segment_file in segment_files:
                print(f"Reading segment: {segment_file}")
                
                # Extract base64 data from PDF-like file
                with open(segment_file, 'rb') as f:
                    content = f.read()
                
                # Find the stream content
                stream_start = content.find(b'stream\n') + 7
                stream_end = content.find(b'\nendstream', stream_start)
                
                if stream_start == -1 or stream_end == -1:
                    print(f"Invalid segment file format: {segment_file}")
                    return False
                
                base64_data = content[stream_start:stream_end].strip()
                chunk_data = base64.b64decode(base64_data)
                combined_data += chunk_data
            
            # Verify file signature
            if not combined_data.startswith(self.file_signature):
                print("Invalid file signature! File may be corrupted or wrong password.")
                return False
            
            # Extract salt and encrypted data
            salt = combined_data[len(self.file_signature):len(self.file_signature)+16]
            encrypted_data = combined_data[len(self.file_signature)+16:]
            
            # Derive key and decrypt
            key = self.derive_key(password, salt)
            fernet = Fernet(key)
            
            # Decrypt and decompress
            compressed_data = fernet.decrypt(encrypted_data)
            original_data = zlib.decompress(compressed_data)
            
            # Write original file
            with open(output_file, 'wb') as f:
                f.write(original_data)
            
            print(f"Decryption complete! File saved as: {output_file}")
            return True
            
        except Exception as e:
            print(f"Decryption error: {e}")
            return False

class FacebookAttachmentDownloader:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://graph.facebook.com/v19.0"
        self.session = requests.Session()
    
    def make_api_request(self, url: str, params: Dict) -> Dict[str, Any]:
        """Make API request with error handling and retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if 'access_token' not in params:
                    params['access_token'] = self.access_token
                    
                response = self.session.get(url, params=params, timeout=30)
                
                if response.status_code == 429:  # Rate limited
                    wait_time = 2 ** attempt
                    print(f"Rate limited. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                    
                if response.status_code != 200:
                    print(f"API Error {response.status_code}: {response.text}")
                    return {'error': f'HTTP {response.status_code}: {response.text}'}
                    
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    print(f"Request failed after {max_retries} attempts: {e}")
                    return {'error': str(e)}
                wait_time = 2 ** attempt
                time.sleep(wait_time)
        return {'error': 'Max retries exceeded'}
    
    def get_conversations(self, limit: int = 20, after: str = None) -> Dict[str, Any]:
        """Get list of conversations with pagination support"""
        url = f"{self.base_url}/me/conversations"
        
        params = {
            'fields': 'participants,updated_time,snippet,message_count',
            'limit': limit
        }
        
        if after:
            params['after'] = after
        
        print("Fetching conversations...")
        return self.make_api_request(url, params)
    
    def get_all_conversations(self, limit: int = 20) -> List[Dict]:
        """Get all conversations with pagination"""
        all_conversations = []
        after = None
        
        while len(all_conversations) < limit:
            result = self.get_conversations(limit=min(20, limit - len(all_conversations)), after=after)
            
            if 'error' in result:
                print(f"Error fetching conversations: {result['error']}")
                break
                
            if 'data' not in result:
                break
                
            all_conversations.extend(result['data'])
            
            # Check if there are more pages
            paging = result.get('paging', {})
            next_url = paging.get('next')
            if not next_url:
                break
                
            # Extract cursor for next page
            after = paging.get('cursors', {}).get('after')
            if not after:
                break
                
            # Be nice to the API
            time.sleep(1)
        
        return all_conversations
    
    def get_messages(self, conversation_id: str, limit: int = 100, after: str = None) -> Dict[str, Any]:
        """Get messages from a conversation"""
        url = f"{self.base_url}/{conversation_id}/messages"
        
        # Use the working field format with curly braces
        params = {
            'fields': 'id,created_time,from,message,attachments{type,file_url,name,size,mime_type}',
            'limit': limit
        }
        
        if after:
            params['after'] = after
        
        print(f"Fetching messages from conversation {conversation_id}...")
        return self.make_api_request(url, params)
    
    def get_all_messages(self, conversation_id: str, limit: int = 1000) -> List[Dict]:
        """Get all messages from a conversation with pagination"""
        all_messages = []
        after = None
        
        while len(all_messages) < limit:
            result = self.get_messages(conversation_id, limit=min(100, limit - len(all_messages)), after=after)
            
            if 'error' in result:
                print(f"Error fetching messages: {result['error']}")
                break
                
            if 'data' not in result or not result['data']:
                break
                
            all_messages.extend(result['data'])
            
            # Check if there are more pages
            paging = result.get('paging', {})
            next_url = paging.get('next')
            if not next_url:
                break
                
            # Extract cursor for next page
            after = paging.get('cursors', {}).get('after')
            if not after:
                break
                
            # Be nice to the API
            time.sleep(1)
        
        return all_messages
    
    def get_all_attachments_for_conversation(self, conversation_id: str, limit_messages: int = 1000) -> List[Dict]:
        """Get all attachments from a specific conversation"""
        messages = self.get_all_messages(conversation_id, limit_messages)
        
        if not messages:
            print(f"No messages found for conversation {conversation_id}")
            return []
        
        attachments = []
        
        for message in messages:
            message_attachments = message.get('attachments', {}).get('data', [])
            
            for attachment in message_attachments:
                # Only process attachments with file_url (avoid stickers, etc.)
                if attachment.get('file_url'):
                    attachment_data = {
                        'message_id': message.get('id'),
                        'created_time': message.get('created_time'),
                        'from': message.get('from', {}).get('name', 'Unknown'),
                        'message_text': message.get('message', ''),
                        'type': attachment.get('type'),
                        'file_url': attachment.get('file_url'),
                        'name': attachment.get('name', 'attachment'),
                        'mime_type': attachment.get('mime_type', ''),
                        'size': attachment.get('size', 0),
                        'conversation_id': conversation_id
                    }
                    attachments.append(attachment_data)
        
        return attachments
    
    def get_all_attachments(self, limit_conversations: int = 10, limit_messages: int = 1000) -> List[Dict]:
        """Get all attachments from all conversations"""
        conversations = self.get_all_conversations(limit_conversations)
        
        if not conversations:
            print("No conversations found")
            return []
        
        all_attachments = []
        
        for i, conversation in enumerate(conversations):
            print(f"Processing conversation {i+1}/{len(conversations)}")
            conversation_id = conversation.get('id')
            
            # Get conversation details for display
            participants = conversation.get('participants', {}).get('data', [])
            participant_names = [p.get('name', 'Unknown') for p in participants]
            conversation_name = ', '.join(participant_names)
            
            print(f"Fetching attachments from: {conversation_name}")
            
            attachments = self.get_all_attachments_for_conversation(conversation_id, limit_messages)
            all_attachments.extend(attachments)
            
            print(f"Found {len(attachments)} attachments in this conversation")
            
            # Be nice to the API
            time.sleep(1)
        
        return all_attachments
    
    def search_attachments_by_name(self, search_pattern: str, limit_conversations: int = 10, limit_messages: int = 1000) -> List[Dict]:
        """Search for attachments that match a name pattern"""
        all_attachments = self.get_all_attachments(limit_conversations, limit_messages)
        
        if not all_attachments:
            return []
        
        # Create a regex pattern to match the search term
        pattern = re.compile(re.escape(search_pattern), re.IGNORECASE)
        
        # Filter attachments that match the pattern
        matching_attachments = [
            attachment for attachment in all_attachments
            if pattern.search(attachment.get('name', ''))
        ]
        
        print(f"Found {len(matching_attachments)} matching attachments")
        return matching_attachments
    
    def download_file(self, file_url: str, file_name: str, download_path: str) -> Optional[str]:
        """Download a file from Facebook URL"""
        os.makedirs(download_path, exist_ok=True)
        
        # Ensure filename is safe
        safe_name = "".join(c for c in file_name if c.isalnum() or c in "._- ")
        file_path = os.path.join(download_path, safe_name)
        
        # Add unique identifier if file already exists
        counter = 1
        while os.path.exists(file_path):
            name, ext = os.path.splitext(safe_name)
            file_path = os.path.join(download_path, f"{name}_{counter}{ext}")
            counter += 1
        
        try:
            print(f"Downloading: {safe_name}")
            
            # Add access token to the file URL for authentication
            parsed_url = urlparse(file_url)
            query_params = parse_qs(parsed_url.query)
            query_params['access_token'] = [self.access_token]
            
            # Rebuild URL with access token
            new_query = urlencode(query_params, doseq=True)
            download_url = urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query,
                parsed_url.fragment
            ))
            
            # Download the file with streaming
            response = requests.get(download_url, stream=True, timeout=60)
            
            if response.status_code != 200:
                print(f"Download failed: HTTP {response.status_code}")
                print(f"Response: {response.text}")
                return None
            
            # Get file size for progress tracking
            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            
            with open(file_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        
                        # Show progress for large files
                        if total_size > 0 and downloaded_size % (1024 * 1024) == 0:
                            progress = (downloaded_size / total_size) * 100
                            print(f"Download progress: {progress:.1f}% ({downloaded_size}/{total_size} bytes)")
            
            file_size = os.path.getsize(file_path)
            print(f"Successfully downloaded: {safe_name} ({file_size} bytes)")
            return file_path
            
        except Exception as e:
            print(f"Error downloading {safe_name}: {e}")
            return None
    
    def download_files_by_name_pattern(self, search_pattern: str, download_path: str, 
                                     limit_conversations: int = 10, limit_messages: int = 1000) -> List[str]:
        """Download all files matching a name pattern"""
        # Search for attachments matching the pattern
        matching_attachments = self.search_attachments_by_name(
            search_pattern, limit_conversations, limit_messages
        )
        
        if not matching_attachments:
            print("No matching files found")
            return []
        
        print(f"Downloading {len(matching_attachments)} matching files...")
        
        # Download the files
        downloaded_files = []
        for attachment in matching_attachments:
            try:
                file_url = attachment.get('file_url')
                file_name = attachment.get('name', 'attachment')
                
                print(f"Downloading: {file_name}")
                
                # Download the file
                file_path = self.download_file(file_url, file_name, download_path)
                
                if file_path:
                    downloaded_files.append(file_path)
                    print(f"Successfully downloaded: {file_name}")
                else:
                    print(f"Failed to download: {file_name}")
                    
            except Exception as e:
                print(f"Error downloading {file_name}: {e}")
        
        return downloaded_files

def init_facebook_service():
    """Initialize Facebook service for downloading files"""
    return FacebookAttachmentDownloader(PAGE_ACCESS_TOKEN)

def request_download(file_url):
    """Request remote server to download and process a file"""
    try:
        response = requests.post(
            f"{REMOTE_SERVER_URL}/start_download",
            json={'file_url': file_url},
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"Server error: {response.status_code} - {response.text}")
            return None
        
        return response.json()
    except Exception as e:
        print(f"Error connecting to server: {e}")
        return None

def check_operation_status(batch_id):
    """Check the status of an operation on the remote server"""
    try:
        response = requests.get(
            f"{REMOTE_SERVER_URL}/operation_status/{batch_id}",
            timeout=10
        )
        
        if response.status_code != 200:
            return None
        
        return response.json()
    except:
        return None

def download_files_by_name_pattern(batch_id, facebook_service):
    """Download files by searching for the name pattern"""
    print(f"Looking for files with pattern: enc_{batch_id}")
    
    # Search for files matching the pattern "enc_{batch_id}"
    search_pattern = f"enc_{batch_id}"
    downloaded_files = facebook_service.download_files_by_name_pattern(
        search_pattern, DOWNLOAD_FOLDER, limit_conversations=20, limit_messages=100
    )
    
    return downloaded_files

def main():
    """Main function"""
    print("Facebook File Transfer Client")
    print("=============================")
    
    # Initialize Facebook service
    facebook_service = init_facebook_service()
    
    # Initialize decryptor
    decryptor = FileDecryptor()
    
    while True:
        print("\nOptions:")
        print("1. Download file from URL")
        print("2. Search and download files by name pattern")
        print("3. Exit")
        
        choice = input("Enter your choice (1-3): ").strip()
        
        if choice == "1":
            file_url = input("Enter the file URL to download: ").strip()
            
            if not file_url:
                print("Invalid URL")
                continue
            
            # Extract the original filename
            original_filename = file_url.split('/')[-1] or "downloaded_file"
            print(f"Will download: {original_filename}")
            
            # Request remote server to process the file
            print("Requesting remote server to process file...")
            result = request_download(file_url)
            
            if not result or 'batch_id' not in result:
                print("Failed to start download process")
                continue
            
            batch_id = result['batch_id']
            print(f"Download started with Batch ID: {batch_id}")
            print("Waiting for remote server to process and upload files...")
            
            # Wait for the operation to complete
            operation_start_time = time.time()
            while True:
                status = check_operation_status(batch_id)
                
                if not status:
                    print("Failed to get operation status")
                    time.sleep(5)
                    continue
                
                if status.get('status') == 'completed':
                    print("Remote processing completed. Downloading files from Facebook...")
                    break
                elif status.get('status') == 'error':
                    print(f"Remote processing failed: {status.get('error', 'Unknown error')}")
                    break
                else:
                    # Only show progress every 10 seconds to avoid spam
                    if time.time() - operation_start_time > 10:
                        print(f"Status: {status.get('current_stage')} - Progress: {status.get('progress', 0)}%")
                        operation_start_time = time.time()
                    time.sleep(2)
            
            # Download files using name pattern search
            downloaded_files = download_files_by_name_pattern(batch_id, facebook_service)
            
            if not downloaded_files:
                print("No files found. The operation may have failed or files may not be visible yet.")
                continue
            
            # Decrypt the files
            print("Decrypting files...")
            output_file = os.path.join(DOWNLOAD_FOLDER, original_filename)
            
            # Create pattern for decryptor (encrypted files start with "enc_")
            pattern = os.path.join(DOWNLOAD_FOLDER, f"enc_{batch_id}_part*.pdf")
            
            success = decryptor.decrypt_file(pattern, output_file, FIXED_PASSWORD)
            
            if success:
                print(f"File successfully decrypted to: {output_file}")
                
                # Clean up encrypted segments
                for file_path in downloaded_files:
                    try:
                        os.remove(file_path)
                    except:
                        pass
            else:
                print("Decryption failed. Encrypted files kept for debugging.")
        
        elif choice == "2":
            # Direct search and download by name pattern
            search_pattern = input("Enter the name pattern to search for (e.g., enc_f49b58b9-5fd9-4012-a062-e283fe680db5): ").strip()
            
            if not search_pattern:
                print("Please enter a search pattern")
                continue
            
            print(f"Searching for files matching pattern: {search_pattern}")
            
            # Search and download files
            downloaded_files = facebook_service.download_files_by_name_pattern(
                search_pattern, DOWNLOAD_FOLDER, limit_conversations=20, limit_messages=100
            )
            
            if downloaded_files:
                print(f"Downloaded {len(downloaded_files)} files:")
                for file_path in downloaded_files:
                    print(f"  - {os.path.basename(file_path)}")
            else:
                print("No files found matching the pattern")
        
        elif choice == "3":
            print("Goodbye!")
            break
        
        else:
            print("Invalid choice")

if __name__ == '__main__':
    main()
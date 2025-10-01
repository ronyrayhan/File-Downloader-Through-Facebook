# import requests
import json
import os
import time
import requests
from config import *
import concurrent.futures
import threading
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

class FacebookService:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://graph.facebook.com/v19.0"
        self.upload_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.lock = threading.Lock()
    
    def debug_request(self, response: requests.Response):
        """Debug API requests"""
        print(f"Status Code: {response.status_code}")
        try:
            print(f"Response JSON: {response.json()}")
        except:
            print(f"Response Text: {response.text}")
    
    def make_api_request(self, url: str, params: Dict, method: str = 'GET', 
                        data: Optional[Dict] = None, files: Optional[Dict] = None) -> Dict[str, Any]:
        """Make API request with error handling and retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if 'access_token' not in params:
                    params['access_token'] = self.access_token
                
                with requests.Session() as session:
                    if method.upper() == 'GET':
                        response = session.get(url, params=params, timeout=30)
                    elif method.upper() == 'POST':
                        response = session.post(url, params=params, data=data, files=files, timeout=30)
                    else:
                        return {'error': f'Unsupported HTTP method: {method}'}
                
                if response.status_code == 429:  # Rate limited
                    wait_time = 2 ** attempt
                    print(f"Rate limited. Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    continue
                    
                if response.status_code != 200:
                    return {'error': f'API Error {response.status_code}: {response.text}'}
                    
                return response.json()
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    print(f"Request failed after {max_retries} attempts: {e}")
                    return {'error': str(e)}
                wait_time = 2 ** attempt
                time.sleep(wait_time)
        return {'error': 'Max retries exceeded'}
    
    def get_conversations(self, limit: int = 20) -> Dict[str, Any]:
        """Get list of conversations"""
        url = f"{self.base_url}/me/conversations"
        
        params = {
            'fields': 'participants,updated_time,snippet,message_count',
            'limit': limit
        }
        
        print("Fetching conversations...")
        return self.make_api_request(url, params)
    
    def get_messages(self, conversation_id: str, limit: int = 100) -> Dict[str, Any]:
        """Get messages from a conversation"""
        url = f"{self.base_url}/{conversation_id}/messages"
        
        params = {
            'fields': 'id,created_time,from,message,attachments{type,file_url,name,size,mime_type,image_data,video_data}',
            'limit': limit
        }
        
        print(f"Fetching messages from conversation {conversation_id}...")
        return self.make_api_request(url, params)
    
    def upload_media(self, file_path: str, media_type: str = 'file') -> Dict[str, Any]:
        """Upload media to Facebook"""
        # Check file size (Facebook limit is 25MB for files)
        file_size = os.path.getsize(file_path)
        if file_size > 25 * 1024 * 1024:  # 25MB in bytes
            return {'error': f'File size {file_size} exceeds 25MB limit'}
        
        url = f"{self.base_url}/me/message_attachments"
        
        params = {
            'access_token': self.access_token
        }
        
        data = {
            'message': json.dumps({
                'attachment': {
                    'type': media_type,
                    'payload': {'is_reusable': True}
                }
            })
        }
        
        try:
            with open(file_path, 'rb') as file:
                files = {'filedata': (os.path.basename(file_path), file)}
                response = self.make_api_request(url, params, 'POST', data=data, files=files)
            
            if 'error' in response:
                return response
                
            attachment_id = response.get('attachment_id')
            
            if not attachment_id:
                return {'error': 'No attachment_id in response', 'response': response}
                
            return {'attachment_id': attachment_id, 'filename': os.path.basename(file_path)}
            
        except Exception as e:
            print(f"Error uploading media {os.path.basename(file_path)}: {e}")
            return {'error': str(e)}
    
    def upload_multiple_files(self, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Upload multiple files in parallel"""
        results = []
        
        # Submit all upload tasks to thread pool
        future_to_file = {
            self.upload_executor.submit(self.upload_media, file_path, 'file'): file_path 
            for file_path in file_paths
        }
        
        # Process completed uploads as they finish
        for future in concurrent.futures.as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                result = future.result(timeout=60)  # 60 second timeout per upload
                results.append(result)
            except Exception as e:
                results.append({'error': str(e), 'filename': os.path.basename(file_path)})
        
        return results
    
    def send_attachment(self, recipient_id: str, attachment_id: str, 
                       attachment_type: str = 'file') -> Dict[str, Any]:
        """Send attachment using attachment ID with proper messaging tags"""
        url = f"{self.base_url}/me/messages"
        
        params = {
            'access_token': self.access_token
        }
        
        # Use the correct messaging tag for file transfers
        payload = {
            'recipient': json.dumps({'id': recipient_id}),
            'message': json.dumps({
                'attachment': {
                    'type': attachment_type,
                    'payload': {
                        'attachment_id': attachment_id
                    }
                }
            }),
            'messaging_type': 'MESSAGE_TAG',
            'tag': 'HUMAN_AGENT'
        }
        
        return self.make_api_request(url, params, 'POST', data=payload)
    
    def send_attachment_with_message(self, recipient_id: str, attachment_id: str, 
                                   attachment_type: str = 'file', message_text: str = '') -> Dict[str, Any]:
        """Send attachment with a text message"""
        # First send the text message
        text_result = self.send_text_message(recipient_id, message_text)
        if 'error' in text_result:
            return text_result
        
        # Then send the attachment
        attachment_result = self.send_attachment(recipient_id, attachment_id, attachment_type)
        return attachment_result
    
    def send_text_message(self, recipient_id: str, message_text: str) -> Dict[str, Any]:
        """Send text message with proper tagging"""
        url = f"{self.base_url}/me/messages"
        
        params = {
            'access_token': self.access_token
        }
        
        payload = {
            'recipient': json.dumps({'id': recipient_id}),
            'message': json.dumps({'text': message_text}),
            'messaging_type': 'MESSAGE_TAG',
            'tag': 'HUMAN_AGENT'
        }
        
        return self.make_api_request(url, params, 'POST', data=payload)
    
    def verify_token(self) -> Dict[str, Any]:
        """Verify if the access token is valid"""
        url = f"{self.base_url}/me"
        params = {
            'access_token': self.access_token,
            'fields': 'id,name'
        }
        
        return self.make_api_request(url, params)
    
    def download_file(self, file_url: str, file_name: str, download_path: str) -> Optional[str]:
        """Download a file from Facebook URL with improved error handling"""
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
            print(f"Downloading: {safe_name} from Facebook...")
            
            # For Facebook downloads, we need to handle the URL properly
            # Facebook file URLs might already have access token or might need one
            parsed_url = urlparse(file_url)
            query_params = parse_qs(parsed_url.query)
            
            # If no access token in URL, add ours
            if 'access_token' not in query_params:
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
                # Try to get error details
                try:
                    error_data = response.json()
                    print(f"Facebook error: {error_data}")
                except:
                    print(f"Response text: {response.text}")
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
                        if total_size > 0:
                            progress = (downloaded_size / total_size) * 100
                            if int(progress) % 10 == 0:  # Print every 10%
                                print(f"Download progress: {progress:.1f}%")
            
            file_size = os.path.getsize(file_path)
            print(f"Successfully downloaded: {safe_name} ({file_size} bytes)")
            return file_path
            
        except requests.exceptions.Timeout:
            print(f"Download timed out: {safe_name}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Network error downloading {safe_name}: {e}")
            return None
        except Exception as e:
            print(f"Error downloading {safe_name}: {e}")
            return None

# Example usage
if __name__ == "__main__":
    # Initialize with your access token
    access_token = FACEBOOK_ACCESS_TOKEN
   
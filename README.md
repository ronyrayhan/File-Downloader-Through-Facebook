A secure, encrypted file transfer system that uses Facebook Messenger as a transport layer. This application allows you to securely transfer files through Facebook by encrypting, splitting, and sending them as PDF-like attachments, then reassembling and decrypting them on the client side.

🛡️ Security Features
End-to-End Encryption: Files are encrypted using Fernet (AES-128-CBC) with PBKDF2 key derivation

File Splitting: Large files are split into manageable chunks (10MB each)

Steganography: Encrypted data is embedded within PDF files for disguise

Secure Transport: Uses Facebook's secure messaging infrastructure

📋 Prerequisites
Python 3.7+

Facebook Page with Messenger enabled

Facebook Developer Account

Page Access Token

🚀 Quick Start
1. Installation
   # Clone the repository
git clone <your-repo-url>
cd facebook-file-transfer

# Install dependencies
pip install requests flask cryptography

2. Configuration
Edit config.py with your credentials:
  PAGE_ACCESS_TOKEN = "your_facebook_page_access_token"
  FIXED_PASSWORD = "your_secure_encryption_password"
  REMOTE_SERVER_URL = "http://your-server-address:9999"
  UPLOAD_FOLDER = 'uploads'
  RECIPIENT_ID = "your_facebook_user_id"
  DOWNLOAD_FOLDER = 'downloads'

3. Running the System
Start the Server:
  python server.py
The server will run on http://0.0.0.0:9999

Run the Client:
python client.py

🎯 Usage Tutorial
Step 1: Set Up Facebook Page
  Create a Facebook Page
  Go to Facebook Developers
  
  Create a new app with "Business" type
  
  Add "Messenger" product to your app
  
  Generate a Page Access Token for your page
  
  Subscribe to webhooks (if needed for advanced features)

Step 2: Find Your Recipient ID
Use the Graph API Explorer
  
  Query /me/accounts to get page info
  
  Or use /me/conversations to find user IDs

Step 3: Transfer a File
Start the client application

  python client.py
Choose option 1 to download from URL

Enter the file URL you want to transfer


Step 4: Manual File Search (Alternative)
If automatic processing fails:
  
  Choose option 2 in the client
  Enter the Batch ID pattern: enc_<your-batch-id>


🔧 Technical Architecture
Server Components
FacebookService: Handles Facebook API interactions

FileEncryptor: Manages encryption and PDF embedding

Flask Server: REST API for processing requests

Background Workers: Parallel file processing

Client Components
FacebookAttachmentDownloader: Searches and downloads from Messenger

FileDecryptor: Reassembles and decrypts files

Interactive CLI: User interface for operations

Encryption Process
Compression: Original file is compressed with zlib

Encryption: Data encrypted with Fernet (AES-128-CBC)

Chunking: Split into 10MB segments

Embedding: Encrypted chunks embedded in PDF structures

Transport: Sent via Facebook Messenger as file attachments

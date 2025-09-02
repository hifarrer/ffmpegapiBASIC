import os
import logging
from replit.object_storage import Client

def upload_to_storage(local_file_path, storage_filename):
    """
    Upload a file to Replit App Storage
    Returns the public URL if successful, None if failed
    """
    try:
        # Check if file exists locally
        if not os.path.exists(local_file_path):
            logging.error(f"Local file not found: {local_file_path}")
            return None
        
        # Initialize Replit Object Storage client
        client = Client()
        
        # Upload the file to the storage bucket
        logging.info(f"Uploading {storage_filename} to Replit App Storage...")
        
        # Upload from local file
        client.upload_from_filename(storage_filename, local_file_path)
        
        # In Replit App Storage, we need to construct the URL manually
        # Files are accessible through a public URL pattern
        # For production, use the production domain
        if os.environ.get('REPLIT_DEPLOYMENT'):
            # In production, files are served from the app storage CDN
            download_url = f"https://ffmpegapi.net/api/storage/{storage_filename}"
        else:
            # In development
            download_url = f"/api/storage/{storage_filename}"
        
        logging.info(f"Successfully uploaded {storage_filename} to Replit App Storage")
        logging.info(f"File will be accessible at: {download_url}")
        
        return download_url
        
    except Exception as e:
        logging.error(f"Failed to upload {storage_filename} to storage: {str(e)}")
        return None

def get_storage_download_url(storage_filename):
    """
    Get the download URL for a file in storage
    """
    try:
        # Check if file exists in storage
        client = Client()
        files = client.list()
        file_exists = any(f.name == storage_filename for f in files)
        
        if not file_exists:
            logging.error(f"File {storage_filename} not found in storage")
            return None
        
        # Return the appropriate URL based on environment
        if os.environ.get('REPLIT_DEPLOYMENT'):
            # In production, files are served from the app storage CDN
            return f"https://ffmpegapi.net/api/storage/{storage_filename}"
        else:
            # In development
            if os.environ.get('REPLIT_DEV_DOMAIN'):
                return f"https://{os.environ['REPLIT_DEV_DOMAIN']}/api/storage/{storage_filename}"
            else:
                return f"/api/storage/{storage_filename}"
    except Exception as e:
        logging.error(f"Failed to get download URL for {storage_filename}: {str(e)}")
        return None

def download_from_storage(storage_filename, local_file_path):
    """
    Download a file from Replit App Storage to local filesystem
    """
    try:
        client = Client()
        
        # Download the file
        data = client.download_as_bytes(storage_filename)
        
        # Save to local file
        with open(local_file_path, 'wb') as f:
            f.write(data)
        
        logging.info(f"Successfully downloaded {storage_filename} from storage")
        return True
    except Exception as e:
        logging.error(f"Failed to download {storage_filename} from storage: {str(e)}")
        return False

def list_storage_files():
    """
    List all files in the storage bucket
    """
    try:
        client = Client()
        files = client.list()
        return files
    except Exception as e:
        logging.error(f"Failed to list storage files: {str(e)}")
        return []
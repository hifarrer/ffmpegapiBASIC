import os
import logging
from google.cloud import storage
from google.auth.credentials import AnonymousCredentials

def upload_to_storage(local_file_path, storage_filename):
    """
    Upload a file to Replit App Storage (Google Cloud Storage)
    Returns the public URL if successful, None if failed
    """
    try:
        # Check if bucket is configured
        bucket_name = os.environ.get('REPLIT_BUCKET_NAME')
        if not bucket_name:
            logging.error("No bucket configured. Please create a bucket in Replit App Storage.")
            return None
        
        # Initialize Google Cloud Storage client
        # Replit App Storage automatically handles authentication
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        
        # Upload the file
        blob = bucket.blob(storage_filename)
        blob.upload_from_filename(local_file_path)
        
        # Make the blob publicly readable
        blob.make_public()
        
        # Return the public URL
        public_url = blob.public_url
        logging.info(f"Successfully uploaded {storage_filename} to storage: {public_url}")
        return public_url
        
    except Exception as e:
        logging.error(f"Failed to upload {storage_filename} to storage: {str(e)}")
        return None

def get_storage_download_url(storage_filename):
    """
    Get the public download URL for a file in storage
    """
    try:
        bucket_name = os.environ.get('REPLIT_BUCKET_NAME')
        if not bucket_name:
            return None
            
        # For public buckets, construct the public URL directly
        return f"https://storage.googleapis.com/{bucket_name}/{storage_filename}"
        
    except Exception as e:
        logging.error(f"Failed to get download URL for {storage_filename}: {str(e)}")
        return None
# FFMPEG Video Merger

## Overview

This is a comprehensive web-based video processing tool that provides seven main functionalities using FFMPEG with a secure authentication system:

1. **Image & Audio Merger** (`/api/merge_image_audio`): Creates videos by combining an image file with an audio file
2. **Video Merger** (`/api/merge_videos`): Concatenates multiple videos from URLs into a single video, with optional audio replacement and output dimensions to handle videos with different aspect ratios
3. **Picture-in-Picture** (`/api/picture_in_picture`): Creates picture-in-picture videos by overlaying one video on top of another with customizable position, scale, and audio options (mute, use video 1 audio, or use video 2 audio)
4. **Add Subtitles** (`/api/add_subtitles`): Burns ASS subtitle files directly into videos with full styling support
5. **Split Audio** (`/api/split_audio`): Splits audio files into equal parts with customizable segment count
6. **Trim Audio** (`/api/trim_audio`): Trims audio files to exact durations with optional fade-out effects
7. **Convert to Vertical** (`/api/convert_to_vertical`): Converts horizontal videos to vertical format (3:4 or 9:16) optimized for mobile viewing, with automatic aspect ratio detection and optional watermark placement

**Authentication & API Keys**: All API endpoints now require authentication via API keys. Users can register for accounts and generate multiple API keys through a dashboard. The site provides a default API key for guest usage on the landing page.

**User Profile Management**: Comprehensive user account management with profile editing (username/email), secure password changes, subscription plan viewing with usage tracking, plan upgrade/downgrade options, and secure account deletion with Stripe subscription cancellation.

**API Documentation**: Comprehensive documentation page (`/docs`) available without login, featuring interactive examples, code samples in multiple languages (cURL, Python, JavaScript), and detailed endpoint specifications.

**Admin Panel**: Complete administrative interface (`/admin`) with user management, API key oversight, subscription plan management, Stripe payment integration settings, user subscription monitoring, usage analytics, and system settings. Default credentials: admin/password123.

**Stripe Integration**: Full payment processing system with admin-configurable API keys stored in database, webhook handling for subscription events, subscription lifecycle management, and secure payment processing for monthly/yearly billing cycles.

**Replit App Storage Integration**: Persistent cloud storage using Replit's App Storage (Google Cloud Storage backed) for all processed videos. Files are stored in the `ffmpeg-videos` bucket and served through the `/api/storage/` endpoint, ensuring downloads work reliably in production even with container restarts.

The application features an accordion interface for easy access to all three tools, built with Flask for the backend and vanilla JavaScript for the frontend, using Bootstrap's dark theme for a professional appearance.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

**Frontend Architecture**
- Single-page application using vanilla JavaScript and Bootstrap 5 dark theme
- Class-based JavaScript architecture with the `VideoMerger` class handling all client-side logic
- Responsive design with drag-and-drop file upload capabilities
- Real-time file validation and progress tracking
- Uses Font Awesome icons for enhanced UI/UX

**Backend Architecture**
- Flask-based web server with RESTful API design and authentication system
- User authentication using Flask-Login with PostgreSQL database storage
- API key-based access control for all video processing endpoints
- File upload handling with security measures (file type validation, size limits)
- FFMPEG integration for video processing (server-side command execution)
- Temporary file management system for uploaded and generated content
- Error handling and logging throughout the application flow
- Database models: User (authentication), ApiKey (access control), SubscriptionPlan (pricing tiers), StripeSettings (payment configuration), UserSubscription (subscription tracking)

**File Processing Pipeline**
- **Image & Audio Processing**: Client-side file validation, server-side MIME type validation, FFMPEG video creation with image loop and audio sync
- **Video URL Processing**: URL validation, video downloading from external sources, FFMPEG concatenation with optional audio replacement, aspect ratio validation
- **Picture-in-Picture Processing**: Dual video download, FFMPEG overlay composition with customizable positioning and scaling
- **Subtitle Processing**: ASS subtitle file validation, FFMPEG subtitle burn-in with style preservation
- **Audio Splitting**: Audio file analysis, duration calculation, FFMPEG segment extraction with MP3 encoding
- **Audio Trimming**: Duration validation, FFMPEG trimming with optional fade-out effects
- **Vertical Conversion**: Automatic aspect ratio detection (3:4 vs 9:16), FFMPEG scaling and padding with black bars, optional watermark overlay at top right corner (20% of video width, 20px padding)
- Temporary file storage during processing with automatic cleanup
- Comprehensive error handling and progress tracking

**Security Measures**
- File extension and MIME type validation
- Maximum file size limits (100MB)
- Secure filename handling to prevent path traversal
- Temporary file isolation in dedicated directories

## External Dependencies

**Python Libraries**
- Flask: Web framework for server-side application
- Werkzeug: WSGI utilities for file handling and security

**Frontend Libraries**
- Bootstrap 5: CSS framework with dark theme support
- Font Awesome 6.4.0: Icon library for UI elements

**System Dependencies**
- FFMPEG: Command-line tool for video processing and format conversion
- File system access for temporary file storage and management

**Runtime Environment**
- Python environment with Flask capability
- File system permissions for upload/output directories
- FFMPEG installation required on the host system
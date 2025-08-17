# FFMPEG Video Merger

## Overview

This is a web-based video creation tool that allows users to merge image and audio files into videos using FFMPEG. The application provides a simple interface for uploading an image file (PNG, JPG, JPEG) and an audio file (MP3, WAV, M4A), then processes them server-side to generate a combined video output. The tool is built with Flask for the backend and vanilla JavaScript for the frontend, featuring a dark-themed responsive design.

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
- Flask-based web server with RESTful API design
- File upload handling with security measures (file type validation, size limits)
- FFMPEG integration for video processing (server-side command execution)
- Temporary file management system for uploaded and generated content
- Error handling and logging throughout the application flow

**File Processing Pipeline**
- Client-side file validation before upload
- Server-side MIME type validation for security
- Temporary file storage during processing
- FFMPEG command execution for video generation
- Automatic cleanup of temporary files

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
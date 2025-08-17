// FFMPEG Video Merger - Frontend JavaScript with Tab Support

class VideoMerger {
    constructor() {
        this.initializeImageAudioTab();
        this.initializeVideosTab();
    }

    initializeImageAudioTab() {
        this.imageAudioForm = document.getElementById('imageAudioForm');
        this.imageAudioSubmitBtn = document.getElementById('imageAudioSubmitBtn');
        this.imageAudioProgressContainer = document.getElementById('imageAudioProgressContainer');
        this.imageAudioAlertContainer = document.getElementById('imageAudioAlertContainer');
        this.imageAudioResultContainer = document.getElementById('imageAudioResultContainer');
        this.imageAudioDownloadBtn = document.getElementById('imageAudioDownloadBtn');
        this.imageAudioCleanupBtn = document.getElementById('imageAudioCleanupBtn');
        this.imageAudioResetBtn = document.getElementById('imageAudioResetBtn');
        this.imageAudioCurrentFilename = null;

        // Event listeners for Image & Audio tab
        this.imageAudioForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleImageAudioSubmit();
        });

        document.getElementById('imageFile').addEventListener('change', () => {
            this.validateImageAudioFiles();
        });

        document.getElementById('audioFile').addEventListener('change', () => {
            this.validateImageAudioFiles();
        });

        this.imageAudioCleanupBtn.addEventListener('click', () => {
            this.handleImageAudioCleanup();
        });

        this.imageAudioResetBtn.addEventListener('click', () => {
            this.resetImageAudioForm();
        });

        this.validateImageAudioFiles();
    }

    initializeVideosTab() {
        this.videosForm = document.getElementById('videosForm');
        this.videosSubmitBtn = document.getElementById('videosSubmitBtn');
        this.videosProgressContainer = document.getElementById('videosProgressContainer');
        this.videosAlertContainer = document.getElementById('videosAlertContainer');
        this.videosResultContainer = document.getElementById('videosResultContainer');
        this.videosDownloadBtn = document.getElementById('videosDownloadBtn');
        this.videosCleanupBtn = document.getElementById('videosCleanupBtn');
        this.videosResetBtn = document.getElementById('videosResetBtn');
        this.videosCurrentFilename = null;

        // Event listeners for Videos tab
        this.videosForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleVideosSubmit();
        });

        document.getElementById('addVideoUrlBtn').addEventListener('click', () => {
            this.addVideoUrlInput();
        });

        this.videosCleanupBtn.addEventListener('click', () => {
            this.handleVideosCleanup();
        });

        this.videosResetBtn.addEventListener('click', () => {
            this.resetVideosForm();
        });

        // Initial validation
        this.validateVideosForm();
        this.setupVideoUrlEventListeners();
    }

    validateImageAudioFiles() {
        const imageFile = document.getElementById('imageFile').files[0];
        const audioFile = document.getElementById('audioFile').files[0];

        this.imageAudioSubmitBtn.disabled = !imageFile || !audioFile;

        this.displayFileInfo('imageFile', imageFile);
        this.displayFileInfo('audioFile', audioFile);
    }

    displayFileInfo(inputId, file) {
        const input = document.getElementById(inputId);
        const existingInfo = input.parentNode.querySelector('.file-info');
        
        if (existingInfo) {
            existingInfo.remove();
        }

        if (file) {
            const fileInfo = document.createElement('div');
            fileInfo.className = 'file-info text-muted small mt-1';
            fileInfo.innerHTML = `
                <i class="fas fa-file me-1"></i>
                ${file.name} (${this.formatFileSize(file.size)})
            `;
            input.parentNode.appendChild(fileInfo);
        }
    }

    formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    async handleImageAudioSubmit() {
        const formData = new FormData(this.imageAudioForm);
        
        const imageFile = document.getElementById('imageFile').files[0];
        const audioFile = document.getElementById('audioFile').files[0];
        
        const maxSize = 100 * 1024 * 1024; // 100MB
        
        if (imageFile.size > maxSize) {
            this.showImageAudioAlert('danger', 'Image file is too large. Maximum size is 100MB.');
            return;
        }
        
        if (audioFile.size > maxSize) {
            this.showImageAudioAlert('danger', 'Audio file is too large. Maximum size is 100MB.');
            return;
        }

        this.setImageAudioLoadingState(true);
        this.hideImageAudioAlert();
        this.hideImageAudioResult();

        try {
            const response = await fetch('/api/merge_image_audio', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.success) {
                this.handleImageAudioSuccess(result);
            } else {
                this.handleImageAudioError(result.error);
            }

        } catch (error) {
            console.error('Upload error:', error);
            this.handleImageAudioError('Network error occurred. Please try again.');
        } finally {
            this.setImageAudioLoadingState(false);
        }
    }

    async handleVideosSubmit() {
        const videoUrls = Array.from(document.querySelectorAll('.video-url'))
            .map(input => input.value.trim())
            .filter(url => url);

        if (videoUrls.length < 2) {
            this.showVideosAlert('danger', 'At least 2 video URLs are required.');
            return;
        }

        const formData = new FormData();
        
        // Add video URLs to form data
        videoUrls.forEach((url, index) => {
            formData.append(`video_url_${index}`, url);
        });
        
        // Add optional audio file
        const audioFile = document.getElementById('videosAudioFile').files[0];
        if (audioFile) {
            formData.append('audio', audioFile);
        }

        this.setVideosLoadingState(true);
        this.hideVideosAlert();
        this.hideVideosResult();

        try {
            const response = await fetch('/api/merge_videos', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.success) {
                this.handleVideosSuccess(result);
            } else {
                this.handleVideosError(result.error);
            }

        } catch (error) {
            console.error('Videos merge error:', error);
            this.handleVideosError('Network error occurred. Please try again.');
        } finally {
            this.setVideosLoadingState(false);
        }
    }

    addVideoUrlInput() {
        const container = document.getElementById('videoUrlsContainer');
        const inputGroup = document.createElement('div');
        inputGroup.className = 'input-group mb-2';
        inputGroup.innerHTML = `
            <input type="url" class="form-control video-url" placeholder="https://example.com/video.mp4" required>
            <button type="button" class="btn btn-outline-danger remove-url">
                <i class="fas fa-times"></i>
            </button>
        `;
        container.appendChild(inputGroup);
        
        // Add event listeners to new elements
        this.setupVideoUrlEventListeners();
        this.validateVideosForm();
    }

    setupVideoUrlEventListeners() {
        document.querySelectorAll('.remove-url').forEach(btn => {
            btn.replaceWith(btn.cloneNode(true)); // Remove existing listeners
        });

        document.querySelectorAll('.video-url').forEach(input => {
            input.replaceWith(input.cloneNode(true)); // Remove existing listeners
        });

        // Add new listeners
        document.querySelectorAll('.remove-url').forEach(btn => {
            btn.addEventListener('click', (e) => {
                if (document.querySelectorAll('.video-url').length > 2) {
                    e.target.closest('.input-group').remove();
                    this.validateVideosForm();
                }
            });
        });

        document.querySelectorAll('.video-url').forEach(input => {
            input.addEventListener('input', () => {
                this.validateVideosForm();
            });
        });

        // Update remove button states
        const removeButtons = document.querySelectorAll('.remove-url');
        removeButtons.forEach((btn, index) => {
            btn.disabled = removeButtons.length <= 2;
        });
    }

    validateVideosForm() {
        const videoUrls = Array.from(document.querySelectorAll('.video-url'))
            .map(input => input.value.trim())
            .filter(url => url);

        this.videosSubmitBtn.disabled = videoUrls.length < 2;
    }

    setImageAudioLoadingState(loading) {
        if (loading) {
            this.imageAudioSubmitBtn.disabled = true;
            this.imageAudioSubmitBtn.classList.add('loading');
            this.imageAudioProgressContainer.style.display = 'block';
        } else {
            this.imageAudioSubmitBtn.disabled = false;
            this.imageAudioSubmitBtn.classList.remove('loading');
            this.imageAudioProgressContainer.style.display = 'none';
        }
    }

    setVideosLoadingState(loading) {
        if (loading) {
            this.videosSubmitBtn.disabled = true;
            this.videosSubmitBtn.classList.add('loading');
            this.videosProgressContainer.style.display = 'block';
        } else {
            this.videosSubmitBtn.disabled = false;
            this.videosSubmitBtn.classList.remove('loading');
            this.videosProgressContainer.style.display = 'none';
        }
    }

    handleImageAudioSuccess(result) {
        this.imageAudioCurrentFilename = result.filename;
        this.imageAudioDownloadBtn.href = result.download_url;
        this.imageAudioDownloadBtn.download = result.filename;
        
        this.showImageAudioAlert('success', result.message);
        this.showImageAudioResult();
    }

    handleImageAudioError(errorMessage) {
        this.showImageAudioAlert('danger', `Error: ${errorMessage}`);
    }

    handleVideosSuccess(result) {
        this.videosCurrentFilename = result.filename;
        this.videosDownloadBtn.href = result.download_url;
        this.videosDownloadBtn.download = result.filename;
        
        this.showVideosAlert('success', result.message);
        this.showVideosResult();
    }

    handleVideosError(errorMessage) {
        // Convert line breaks to HTML for better display
        const formattedMessage = errorMessage.replace(/\n/g, '<br>');
        this.showVideosAlert('danger', `Error: ${formattedMessage}`);
    }

    async handleImageAudioCleanup() {
        if (!this.imageAudioCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.imageAudioCurrentFilename}`, {
                method: 'POST'
            });

            const result = await response.json();
            
            if (result.success) {
                this.showImageAudioAlert('info', 'File successfully deleted from server.');
                this.hideImageAudioResult();
                this.imageAudioCurrentFilename = null;
            } else {
                this.showImageAudioAlert('warning', 'Could not delete file from server.');
            }

        } catch (error) {
            console.error('Cleanup error:', error);
            this.showImageAudioAlert('warning', 'Could not delete file from server.');
        }
    }

    async handleVideosCleanup() {
        if (!this.videosCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.videosCurrentFilename}`, {
                method: 'POST'
            });

            const result = await response.json();
            
            if (result.success) {
                this.showVideosAlert('info', 'File successfully deleted from server.');
                this.hideVideosResult();
                this.videosCurrentFilename = null;
            } else {
                this.showVideosAlert('warning', 'Could not delete file from server.');
            }

        } catch (error) {
            console.error('Cleanup error:', error);
            this.showVideosAlert('warning', 'Could not delete file from server.');
        }
    }

    resetImageAudioForm() {
        this.imageAudioForm.reset();
        this.hideImageAudioAlert();
        this.hideImageAudioResult();
        this.validateImageAudioFiles();
        this.imageAudioCurrentFilename = null;
        
        document.querySelectorAll('.file-info').forEach(info => {
            info.remove();
        });
    }

    resetVideosForm() {
        // Reset to 2 URL inputs
        const container = document.getElementById('videoUrlsContainer');
        container.innerHTML = `
            <div class="input-group mb-2">
                <input type="url" class="form-control video-url" placeholder="https://example.com/video1.mp4" required>
                <button type="button" class="btn btn-outline-danger remove-url" disabled>
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="input-group mb-2">
                <input type="url" class="form-control video-url" placeholder="https://example.com/video2.mp4" required>
                <button type="button" class="btn btn-outline-danger remove-url">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        document.getElementById('videosAudioFile').value = '';
        this.hideVideosAlert();
        this.hideVideosResult();
        this.videosCurrentFilename = null;
        
        this.setupVideoUrlEventListeners();
        this.validateVideosForm();
    }

    showImageAudioAlert(type, message) {
        this.imageAudioAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideImageAudioAlert() {
        this.imageAudioAlertContainer.innerHTML = '';
    }

    showImageAudioResult() {
        this.imageAudioResultContainer.style.display = 'block';
    }

    hideImageAudioResult() {
        this.imageAudioResultContainer.style.display = 'none';
    }

    showVideosAlert(type, message) {
        this.videosAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideVideosAlert() {
        this.videosAlertContainer.innerHTML = '';
    }

    showVideosResult() {
        this.videosResultContainer.style.display = 'block';
    }

    hideVideosResult() {
        this.videosResultContainer.style.display = 'none';
    }

    getAlertIcon(type) {
        const icons = {
            success: 'check-circle',
            danger: 'exclamation-triangle',
            warning: 'exclamation-triangle',
            info: 'info-circle'
        };
        return icons[type] || 'info-circle';
    }
}

// Initialize the application when the DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new VideoMerger();
});

// Add some CSS for drag and drop styling
const dragDropStyles = `
    .drag-over {
        border-color: var(--bs-primary) !important;
        background-color: rgba(var(--bs-primary-rgb), 0.1) !important;
    }
`;

const styleSheet = document.createElement('style');
styleSheet.textContent = dragDropStyles;
document.head.appendChild(styleSheet);
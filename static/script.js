// FFMPEG Video Merger - Frontend JavaScript

class VideoMerger {
    constructor() {
        this.form = document.getElementById('uploadForm');
        this.submitBtn = document.getElementById('submitBtn');
        this.progressContainer = document.getElementById('progressContainer');
        this.alertContainer = document.getElementById('alertContainer');
        this.resultContainer = document.getElementById('resultContainer');
        this.downloadBtn = document.getElementById('downloadBtn');
        this.cleanupBtn = document.getElementById('cleanupBtn');
        this.resetBtn = document.getElementById('resetBtn');
        this.currentFilename = null;

        this.initializeEventListeners();
        this.validateFiles();
    }

    initializeEventListeners() {
        // Form submission
        this.form.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSubmit();
        });

        // File input change events
        document.getElementById('imageFile').addEventListener('change', () => {
            this.validateFiles();
        });

        document.getElementById('audioFile').addEventListener('change', () => {
            this.validateFiles();
        });

        // Button click events
        this.cleanupBtn.addEventListener('click', () => {
            this.handleCleanup();
        });

        this.resetBtn.addEventListener('click', () => {
            this.resetForm();
        });

        // File drag and drop enhancement
        this.setupDragAndDrop();
    }

    validateFiles() {
        const imageFile = document.getElementById('imageFile').files[0];
        const audioFile = document.getElementById('audioFile').files[0];

        // Enable/disable submit button based on file selection
        this.submitBtn.disabled = !imageFile || !audioFile;

        // Show file information
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

    setupDragAndDrop() {
        const fileInputs = [
            document.getElementById('imageFile'),
            document.getElementById('audioFile')
        ];

        fileInputs.forEach(input => {
            const parent = input.parentNode;
            
            parent.addEventListener('dragover', (e) => {
                e.preventDefault();
                parent.classList.add('drag-over');
            });

            parent.addEventListener('dragleave', () => {
                parent.classList.remove('drag-over');
            });

            parent.addEventListener('drop', (e) => {
                e.preventDefault();
                parent.classList.remove('drag-over');
                
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    input.files = files;
                    this.validateFiles();
                }
            });
        });
    }

    async handleSubmit() {
        const formData = new FormData(this.form);
        
        // Validate file sizes
        const imageFile = document.getElementById('imageFile').files[0];
        const audioFile = document.getElementById('audioFile').files[0];
        
        const maxSize = 100 * 1024 * 1024; // 100MB
        
        if (imageFile.size > maxSize) {
            this.showAlert('danger', 'Image file is too large. Maximum size is 100MB.');
            return;
        }
        
        if (audioFile.size > maxSize) {
            this.showAlert('danger', 'Audio file is too large. Maximum size is 100MB.');
            return;
        }

        this.setLoadingState(true);
        this.hideAlert();
        this.hideResult();

        try {
            const response = await fetch('/api/merge', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (result.success) {
                this.handleSuccess(result);
            } else {
                this.handleError(result.error);
            }

        } catch (error) {
            console.error('Upload error:', error);
            this.handleError('Network error occurred. Please try again.');
        } finally {
            this.setLoadingState(false);
        }
    }

    setLoadingState(loading) {
        if (loading) {
            this.submitBtn.disabled = true;
            this.submitBtn.classList.add('loading');
            this.progressContainer.style.display = 'block';
            
            // Update button text
            const btnText = this.submitBtn.querySelector('.btn-text');
            if (!btnText) {
                this.submitBtn.innerHTML = `<span class="btn-text">${this.submitBtn.innerHTML}</span>`;
            }
        } else {
            this.submitBtn.disabled = false;
            this.submitBtn.classList.remove('loading');
            this.progressContainer.style.display = 'none';
        }
    }

    handleSuccess(result) {
        this.currentFilename = result.filename;
        this.downloadBtn.href = result.download_url;
        this.downloadBtn.download = result.filename;
        
        this.showAlert('success', result.message);
        this.showResult();
    }

    handleError(errorMessage) {
        this.showAlert('danger', `Error: ${errorMessage}`);
    }

    async handleCleanup() {
        if (!this.currentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.currentFilename}`, {
                method: 'POST'
            });

            const result = await response.json();
            
            if (result.success) {
                this.showAlert('info', 'File successfully deleted from server.');
                this.hideResult();
                this.currentFilename = null;
            } else {
                this.showAlert('warning', 'Could not delete file from server.');
            }

        } catch (error) {
            console.error('Cleanup error:', error);
            this.showAlert('warning', 'Could not delete file from server.');
        }
    }

    resetForm() {
        this.form.reset();
        this.hideAlert();
        this.hideResult();
        this.validateFiles();
        this.currentFilename = null;
        
        // Remove file info displays
        document.querySelectorAll('.file-info').forEach(info => {
            info.remove();
        });
    }

    showAlert(type, message) {
        this.alertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideAlert() {
        this.alertContainer.innerHTML = '';
    }

    showResult() {
        this.resultContainer.style.display = 'block';
    }

    hideResult() {
        this.resultContainer.style.display = 'none';
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

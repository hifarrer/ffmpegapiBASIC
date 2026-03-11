// FFMPEG API - Frontend JavaScript with Tab Support

class VideoMerger {
    constructor() {
        this.initializeImageAudioTab();
        this.initializeVideosTab();
        this.initializeVideoLoopTab();
        this.initializePipTab();
        this.initializeSubtitlesTab();
        this.initializeSplitAudioTab();
        this.initializeSplitAudioSegmentsTab();
        this.initializeSplitAudioTimeTab();
        this.initializeTrimAudioTab();
        this.initializeTrimVideoTab();
        this.initializeSplitVideoTab();
        this.initializeFirstFrameTab();
        this.initializeLastFrameTab();
        this.initializeConvertVerticalTab();
        this.initializeAutoCaptionTab();
        this.initializeTextOverlayTab();
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

    initializeVideoLoopTab() {
        this.videoLoopForm = document.getElementById('videoLoopForm');
        if (!this.videoLoopForm) {
            return;
        }

        this.videoLoopSubmitBtn = document.getElementById('videoLoopSubmitBtn');
        this.videoLoopProgressContainer = document.getElementById('videoLoopProgressContainer');
        this.videoLoopAlertContainer = document.getElementById('videoLoopAlertContainer');
        this.videoLoopResultContainer = document.getElementById('videoLoopResultContainer');
        this.videoLoopDownloadBtn = document.getElementById('videoLoopDownloadBtn');
        this.videoLoopResetBtn = document.getElementById('videoLoopResetBtn');
        this.videoLoopDetails = document.getElementById('videoLoopDetails');
        this.videoLoopFilename = null;

        this.videoLoopForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleVideoLoopSubmit();
        });

        this.videoLoopResetBtn.addEventListener('click', () => {
            this.resetVideoLoopForm();
        });
    }

    initializePipTab() {
        this.pipForm = document.getElementById('pipForm');
        this.pipSubmitBtn = document.getElementById('pipSubmitBtn');
        this.pipProgressContainer = document.getElementById('pipProgressContainer');
        this.pipAlertContainer = document.getElementById('pipAlertContainer');
        this.pipResultContainer = document.getElementById('pipResultContainer');
        this.pipDownloadBtn = document.getElementById('pipDownloadBtn');
        this.pipCleanupBtn = document.getElementById('pipCleanupBtn');
        this.pipResetBtn = document.getElementById('pipResetBtn');
        this.pipCurrentFilename = null;

        // Event listeners for Picture-in-Picture tab
        this.pipForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handlePipSubmit();
        });

        document.getElementById('mainVideoUrl').addEventListener('input', () => {
            this.validatePipForm();
        });

        document.getElementById('pipVideoUrl').addEventListener('input', () => {
            this.validatePipForm();
        });

        this.pipCleanupBtn.addEventListener('click', () => {
            this.handlePipCleanup();
        });

        this.pipResetBtn.addEventListener('click', () => {
            this.resetPipForm();
        });

        // Initial validation
        this.validatePipForm();
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

        // Add API key to form data
        if (window.API_KEY) {
            formData.append('api_key', window.API_KEY);
        }

        this.setImageAudioLoadingState(true);
        this.hideImageAudioAlert();
        this.hideImageAudioResult();

        try {
            const response = await fetch('/api/merge_image_audio', {
                method: 'POST',
                headers: {
                    'X-API-Key': window.API_KEY || ''
                },
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

    async handleVideoLoopSubmit() {
        const videoUrlInput = document.getElementById('videoLoopVideoUrl');
        const loopsInput = document.getElementById('videoLoopLoops');
        const audioUrlInput = document.getElementById('videoLoopAudioUrl');

        const video_url = videoUrlInput.value.trim();
        const number_of_loops_raw = loopsInput.value.trim();
        const audio_url = audioUrlInput.value.trim();

        if (!video_url) {
            this.showVideoLoopAlert('danger', 'Video URL is required.');
            return;
        }

        const payload = { video_url };

        if (number_of_loops_raw !== '') {
            const parsed = parseInt(number_of_loops_raw, 10);
            if (isNaN(parsed) || parsed <= 0) {
                this.showVideoLoopAlert('danger', 'Number of loops must be a positive integer.');
                return;
            }
            payload.number_of_loops = parsed;
        } else if (!audio_url) {
            this.showVideoLoopAlert('danger', 'Either number of loops or an audio URL is required.');
            return;
        }

        if (audio_url) {
            payload.audio_url = audio_url;
        }

        this.showVideoLoopProgress();
        this.hideVideoLoopAlert();
        this.hideVideoLoopResult();

        try {
            const response = await fetch('/api/video_loop', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(payload)
            });

            const result = await response.json();

            if (result.success) {
                this.videoLoopFilename = result.filename;
                this.videoLoopDownloadBtn.href = result.download_url;
                this.videoLoopDownloadBtn.download = result.filename;

                const loops = result.loops;
                const videoDur = result.video_duration_seconds;
                const audioDur = result.audio_duration_seconds;
                const estDur = result.estimated_total_duration_seconds;

                const parts = [];
                if (typeof loops === 'number') parts.push(`Loops: ${loops}`);
                if (typeof videoDur === 'number') parts.push(`Video duration: ${videoDur.toFixed(2)}s`);
                if (typeof audioDur === 'number') parts.push(`Audio duration: ${audioDur.toFixed(2)}s`);
                if (typeof estDur === 'number') parts.push(`Estimated looped duration: ${estDur.toFixed(2)}s`);

                if (this.videoLoopDetails) {
                    this.videoLoopDetails.textContent = parts.join(' • ');
                }

                this.showVideoLoopResult();
                this.showVideoLoopAlert('success', 'Loop video created successfully.');
            } else {
                this.showVideoLoopAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Video loop error:', error);
            this.showVideoLoopAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideVideoLoopProgress();
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

        const requestData = {
            video_urls: videoUrls
        };
        
        // Add optional audio URL
        const audioUrl = document.getElementById('videosAudioUrl').value.trim();
        if (audioUrl) {
            requestData.audio_url = audioUrl;
        }
        
        // Add optional subtitle URL
        const subtitleUrl = document.getElementById('videosSubtitleUrl').value.trim();
        if (subtitleUrl) {
            requestData.subtitle_url = subtitleUrl;
        }
        
        // Add optional watermark URL
        const watermarkUrl = document.getElementById('videosWatermarkUrl').value.trim();
        if (watermarkUrl) {
            requestData.watermark_url = watermarkUrl;
        }
        
        // Add optional dimensions
        const dimensions = document.getElementById('videosDimensions').value.trim();
        if (dimensions) {
            requestData.dimensions = dimensions;
        }

        this.setVideosLoadingState(true);
        this.hideVideosAlert();
        this.hideVideosResult();

        try {
            const response = await fetch('/api/merge_videos', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY || ''
                },
                body: JSON.stringify(requestData)
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

    resetVideoLoopForm() {
        if (!this.videoLoopForm) return;
        this.videoLoopForm.reset();
        this.hideVideoLoopAlert();
        this.hideVideoLoopResult();
        this.hideVideoLoopProgress();
        this.videoLoopFilename = null;
        if (this.videoLoopDetails) {
            this.videoLoopDetails.textContent = '';
        }
    }

    showVideoLoopProgress() {
        if (this.videoLoopProgressContainer) {
            this.videoLoopProgressContainer.style.display = 'block';
        }
        if (this.videoLoopSubmitBtn) {
            this.videoLoopSubmitBtn.disabled = true;
        }
    }

    hideVideoLoopProgress() {
        if (this.videoLoopProgressContainer) {
            this.videoLoopProgressContainer.style.display = 'none';
        }
        if (this.videoLoopSubmitBtn) {
            this.videoLoopSubmitBtn.disabled = false;
        }
    }

    showVideoLoopAlert(type, message) {
        if (!this.videoLoopAlertContainer) return;
        this.videoLoopAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideVideoLoopAlert() {
        if (this.videoLoopAlertContainer) {
            this.videoLoopAlertContainer.innerHTML = '';
        }
    }

    showVideoLoopResult() {
        if (this.videoLoopResultContainer) {
            this.videoLoopResultContainer.style.display = 'block';
        }
    }

    hideVideoLoopResult() {
        if (this.videoLoopResultContainer) {
            this.videoLoopResultContainer.style.display = 'none';
        }
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

    isValidUrl(string) {
        try {
            const url = new URL(string);
            return url.protocol === 'http:' || url.protocol === 'https:';
        } catch (_) {
            return false;
        }
    }

    // Picture-in-Picture methods
    async handlePipSubmit() {
        const mainVideoUrl = document.getElementById('mainVideoUrl').value.trim();
        const pipVideoUrl = document.getElementById('pipVideoUrl').value.trim();

        if (!mainVideoUrl || !pipVideoUrl) {
            this.showPipAlert('danger', 'Both video URLs are required.');
            return;
        }

        const requestData = {
            main_video_url: mainVideoUrl,
            pip_video_url: pipVideoUrl,
            position: document.getElementById('pipPosition').value,
            scale: document.getElementById('pipScale').value,
            audio_option: document.getElementById('pipAudio').value
        };

        this.setPipLoadingState(true);
        this.hidePipAlert();
        this.hidePipResult();

        try {
            const response = await fetch('/api/picture_in_picture', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY || ''
                },
                body: JSON.stringify(requestData)
            });

            const result = await response.json();

            if (result.success) {
                this.handlePipSuccess(result);
            } else {
                this.handlePipError(result.error);
            }

        } catch (error) {
            console.error('PiP processing error:', error);
            this.handlePipError('Network error occurred. Please try again.');
        } finally {
            this.setPipLoadingState(false);
        }
    }

    validatePipForm() {
        const mainVideoUrl = document.getElementById('mainVideoUrl').value.trim();
        const pipVideoUrl = document.getElementById('pipVideoUrl').value.trim();
        this.pipSubmitBtn.disabled = !mainVideoUrl || !pipVideoUrl;
    }

    setPipLoadingState(loading) {
        if (loading) {
            this.pipSubmitBtn.disabled = true;
            this.pipSubmitBtn.classList.add('loading');
            this.pipProgressContainer.style.display = 'block';
        } else {
            this.pipSubmitBtn.disabled = false;
            this.pipSubmitBtn.classList.remove('loading');
            this.pipProgressContainer.style.display = 'none';
        }
    }

    handlePipSuccess(result) {
        this.pipCurrentFilename = result.filename;
        this.pipDownloadBtn.href = result.download_url;
        this.pipDownloadBtn.download = result.filename;
        
        this.showPipAlert('success', result.message);
        this.showPipResult();
    }

    handlePipError(errorMessage) {
        this.showPipAlert('danger', `Error: ${errorMessage}`);
    }

    async handlePipCleanup() {
        if (!this.pipCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.pipCurrentFilename}`, {
                method: 'POST'
            });

            const result = await response.json();
            
            if (result.success) {
                this.showPipAlert('info', 'File successfully deleted from server.');
                this.hidePipResult();
                this.pipCurrentFilename = null;
            } else {
                this.showPipAlert('warning', 'Could not delete file from server.');
            }

        } catch (error) {
            console.error('Cleanup error:', error);
            this.showPipAlert('warning', 'Could not delete file from server.');
        }
    }

    resetPipForm() {
        this.pipForm.reset();
        document.getElementById('pipPosition').value = 'bottom-right';
        document.getElementById('pipScale').value = 'iw/4:ih/4';
        this.hidePipAlert();
        this.hidePipResult();
        this.validatePipForm();
        this.pipCurrentFilename = null;
    }

    showPipAlert(type, message) {
        this.pipAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hidePipAlert() {
        this.pipAlertContainer.innerHTML = '';
    }

    showPipResult() {
        this.pipResultContainer.style.display = 'block';
    }

    hidePipResult() {
        this.pipResultContainer.style.display = 'none';
    }

    initializeSubtitlesTab() {
        this.subtitlesForm = document.getElementById('subtitlesForm');
        this.subtitlesProgressContainer = document.getElementById('subtitlesProcessing');
        this.subtitlesAlertContainer = document.getElementById('subtitlesAlertContainer');
        this.subtitlesResultContainer = document.getElementById('subtitlesResultContainer');
        this.subtitlesDownloadBtn = document.getElementById('subtitlesDownloadBtn');
        this.subtitlesCleanupBtn = document.getElementById('subtitlesCleanupBtn');
        this.subtitlesResetBtn = document.getElementById('subtitlesResetBtn');
        this.subtitlesCurrentFilename = null;

        // Event listeners for Subtitles tab
        this.subtitlesForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSubtitlesSubmit();
        });

        document.getElementById('subtitlesVideoUrl').addEventListener('input', () => {
            this.validateSubtitlesForm();
        });

        document.getElementById('subtitleFileUrl').addEventListener('input', () => {
            this.validateSubtitlesForm();
        });

        this.subtitlesCleanupBtn.addEventListener('click', () => {
            this.handleSubtitlesCleanup();
        });

        this.subtitlesResetBtn.addEventListener('click', () => {
            this.resetSubtitlesForm();
        });

        // Initial validation
        this.validateSubtitlesForm();
    }

    validateSubtitlesForm() {
        const videoUrl = document.getElementById('subtitlesVideoUrl').value;
        const subtitleUrl = document.getElementById('subtitleFileUrl').value;
        
        const isValidVideoUrl = this.isValidUrl(videoUrl);
        const isValidSubtitleUrl = this.isValidUrl(subtitleUrl);
        
        const submitBtn = this.subtitlesForm.querySelector('button[type="submit"]');
        submitBtn.disabled = !isValidVideoUrl || !isValidSubtitleUrl;
    }

    async handleSubtitlesSubmit() {
        this.showSubtitlesProgress();
        this.hideSubtitlesAlert();
        this.hideSubtitlesResult();

        const formData = new FormData(this.subtitlesForm);
        const data = {
            video_url: formData.get('video_url'),
            subtitle_url: formData.get('subtitle_url')
        };

        try {
            const response = await fetch('/api/add_subtitles', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();
            this.hideSubtitlesProgress();

            if (result.success) {
                this.subtitlesCurrentFilename = result.filename;
                this.subtitlesDownloadBtn.href = result.download_url;
                this.showSubtitlesResult();
                this.showSubtitlesAlert('success', result.message || 'Subtitles added successfully!');
            } else {
                this.showSubtitlesAlert('danger', result.error || 'Failed to add subtitles.');
            }
        } catch (error) {
            this.hideSubtitlesProgress();
            console.error('Error:', error);
            this.showSubtitlesAlert('danger', 'Network error. Please try again.');
        }
    }

    async handleSubtitlesCleanup() {
        if (!this.subtitlesCurrentFilename) {
            this.showSubtitlesAlert('warning', 'No file to delete.');
            return;
        }

        try {
            const response = await fetch(`/cleanup/${this.subtitlesCurrentFilename}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                this.showSubtitlesAlert('success', 'File deleted from server successfully.');
                this.hideSubtitlesResult();
                this.subtitlesCurrentFilename = null;
            } else {
                this.showSubtitlesAlert('warning', 'File may have already been deleted.');
            }
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showSubtitlesAlert('warning', 'Could not delete file from server.');
        }
    }

    resetSubtitlesForm() {
        this.subtitlesForm.reset();
        this.hideSubtitlesAlert();
        this.hideSubtitlesResult();
        this.validateSubtitlesForm();
        this.subtitlesCurrentFilename = null;
    }

    showSubtitlesProgress() {
        this.subtitlesProgressContainer.style.display = 'block';
    }

    hideSubtitlesProgress() {
        this.subtitlesProgressContainer.style.display = 'none';
    }

    showSubtitlesAlert(type, message) {
        this.subtitlesAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideSubtitlesAlert() {
        this.subtitlesAlertContainer.innerHTML = '';
    }

    showSubtitlesResult() {
        this.subtitlesResultContainer.style.display = 'block';
    }

    hideSubtitlesResult() {
        this.subtitlesResultContainer.style.display = 'none';
    }

    initializeSplitAudioTab() {
        this.splitAudioForm = document.getElementById('splitAudioForm');
        this.splitAudioSubmitBtn = document.getElementById('splitAudioSubmitBtn');
        this.splitAudioProgressContainer = document.getElementById('splitAudioProgressContainer');
        this.splitAudioAlertContainer = document.getElementById('splitAudioAlertContainer');
        this.splitAudioResultContainer = document.getElementById('splitAudioResultContainer');
        this.splitAudioResetBtn = document.getElementById('splitAudioResetBtn');
        this.splitAudioParts = [];

        // Event listeners for Split Audio tab
        this.splitAudioForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSplitAudioSubmit();
        });

        document.getElementById('audioUrl').addEventListener('input', () => {
            this.validateSplitAudioForm();
        });

        document.getElementById('audioParts').addEventListener('input', () => {
            this.validateSplitAudioForm();
        });

        const downloadAllBtn = document.getElementById('splitAudioDownloadAllBtn');
        if (downloadAllBtn) {
            downloadAllBtn.addEventListener('click', () => {
                this.downloadAllAudioParts();
            });
        }

        this.splitAudioResetBtn.addEventListener('click', () => {
            this.resetSplitAudioForm();
        });

        // Initial validation
        this.validateSplitAudioForm();
    }

    validateSplitAudioForm() {
        const audioUrl = document.getElementById('audioUrl').value;
        const audioParts = document.getElementById('audioParts').value;
        
        const isValidUrl = this.isValidUrl(audioUrl);
        const isValidParts = audioParts >= 2 && audioParts <= 20;
        
        this.splitAudioSubmitBtn.disabled = !isValidUrl || !isValidParts;
    }

    async handleSplitAudioSubmit() {
        this.showSplitAudioProgress();
        this.hideSplitAudioAlert();
        this.hideSplitAudioResult();

        const formData = new FormData(this.splitAudioForm);
        const data = {
            audio_url: formData.get('audio_url'),
            parts: parseInt(formData.get('parts'))
        };

        try {
            const response = await fetch('/api/split_audio', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();
            this.hideSplitAudioProgress();

            if (result.success) {
                this.splitAudioParts = result.audio_parts;
                document.getElementById('splitPartsCount').textContent = result.parts;
                this.displaySplitAudioParts(result.audio_parts);
                this.showSplitAudioResult();
                this.showSplitAudioAlert('success', `Audio successfully split into ${result.parts} parts!`);
            } else {
                this.showSplitAudioAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            this.hideSplitAudioProgress();
            this.showSplitAudioAlert('danger', `Network error: ${error.message}`);
        }
    }

    displaySplitAudioParts(parts) {
        const container = document.getElementById('splitAudioParts');
        container.innerHTML = parts.map((part, index) => `
            <div class="d-flex align-items-center justify-content-between border rounded p-2 mb-2">
                <div>
                    <strong>Part ${index + 1}:</strong> ${part.part}
                </div>
                <a href="${part.download_url}" class="btn btn-sm btn-outline-success" download>
                    <i class="fas fa-download me-1"></i>Download
                </a>
            </div>
        `).join('');
    }

    downloadAllAudioParts() {
        this.splitAudioParts.forEach((part, index) => {
            setTimeout(() => {
                const link = document.createElement('a');
                link.href = part.download_url;
                link.download = part.part;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            }, index * 500); // Delay to avoid overwhelming the browser
        });
    }

    resetSplitAudioForm() {
        this.splitAudioForm.reset();
        document.getElementById('audioParts').value = 4; // Reset to default
        this.hideSplitAudioProgress();
        this.hideSplitAudioAlert();
        this.hideSplitAudioResult();
        this.splitAudioParts = [];
        this.validateSplitAudioForm();
    }

    showSplitAudioProgress() {
        this.splitAudioProgressContainer.style.display = 'block';
        this.splitAudioSubmitBtn.disabled = true;
    }

    hideSplitAudioProgress() {
        this.splitAudioProgressContainer.style.display = 'none';
        this.splitAudioSubmitBtn.disabled = false;
    }

    showSplitAudioAlert(type, message) {
        this.splitAudioAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideSplitAudioAlert() {
        this.splitAudioAlertContainer.innerHTML = '';
    }

    showSplitAudioResult() {
        this.splitAudioResultContainer.style.display = 'block';
    }

    hideSplitAudioResult() {
        this.splitAudioResultContainer.style.display = 'none';
    }

    // Split Audio by Segments Tab Methods
    initializeSplitAudioSegmentsTab() {
        this.splitAudioSegmentsForm = document.getElementById('splitAudioSegmentsForm');
        this.splitAudioSegmentsSubmitBtn = document.getElementById('splitAudioSegmentsSubmitBtn');
        this.splitAudioSegmentsProgressContainer = document.getElementById('splitAudioSegmentsProgressContainer');
        this.splitAudioSegmentsAlertContainer = document.getElementById('splitAudioSegmentsAlertContainer');
        this.splitAudioSegmentsResultContainer = document.getElementById('splitAudioSegmentsResultContainer');
        this.splitAudioSegmentsResetBtn = document.getElementById('splitAudioSegmentsResetBtn');
        this.splitAudioSegmentsParts = [];

        // Event listeners for Split Audio by Segments tab
        this.splitAudioSegmentsForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSplitAudioSegmentsSubmit();
        });

        document.getElementById('segmentAudioUrl').addEventListener('input', () => {
            this.validateSplitAudioSegmentsForm();
        });

        document.getElementById('segmentDuration').addEventListener('input', () => {
            this.validateSplitAudioSegmentsForm();
        });

        const downloadAllBtn = document.getElementById('splitAudioSegmentsDownloadAllBtn');
        if (downloadAllBtn) {
            downloadAllBtn.addEventListener('click', () => {
                this.downloadAllAudioSegments();
            });
        }

        this.splitAudioSegmentsResetBtn.addEventListener('click', () => {
            this.resetSplitAudioSegmentsForm();
        });

        // Initial validation
        this.validateSplitAudioSegmentsForm();
    }

    validateSplitAudioSegmentsForm() {
        const audioUrl = document.getElementById('segmentAudioUrl').value;
        const segmentDuration = document.getElementById('segmentDuration').value;
        
        const isValidUrl = this.isValidUrl(audioUrl);
        const isValidDuration = segmentDuration >= 1 && segmentDuration <= 3600;
        
        this.splitAudioSegmentsSubmitBtn.disabled = !isValidUrl || !isValidDuration;
    }

    async handleSplitAudioSegmentsSubmit() {
        this.showSplitAudioSegmentsProgress();
        this.hideSplitAudioSegmentsAlert();
        this.hideSplitAudioSegmentsResult();

        const formData = new FormData(this.splitAudioSegmentsForm);
        const data = {
            audio_url: formData.get('audio_url'),
            segment_duration: parseFloat(formData.get('segment_duration'))
        };

        try {
            const response = await fetch('/api/split_audio_segments', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();
            this.hideSplitAudioSegmentsProgress();

            if (result.success) {
                this.splitAudioSegmentsParts = result.segments;
                document.getElementById('splitSegmentsCount').textContent = result.total_segments;
                document.getElementById('splitSegmentDuration').textContent = result.segment_duration;
                this.displaySplitAudioSegments(result.segments);
                this.showSplitAudioSegmentsResult();
                this.showSplitAudioSegmentsAlert('success', `Audio successfully split into ${result.total_segments} segments!`);
            } else {
                this.showSplitAudioSegmentsAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            this.hideSplitAudioSegmentsProgress();
            this.showSplitAudioSegmentsAlert('danger', `Network error: ${error.message}`);
        }
    }

    displaySplitAudioSegments(segments) {
        const container = document.getElementById('splitAudioSegmentsParts');
        container.innerHTML = segments.map((segment, index) => `
            <div class="d-flex align-items-center justify-content-between border rounded p-2 mb-2">
                <div>
                    <strong>Segment ${index + 1}:</strong> ${segment.segment}
                </div>
                <a href="${segment.download_url}" class="btn btn-sm btn-outline-success" download>
                    <i class="fas fa-download me-1"></i>Download
                </a>
            </div>
        `).join('');
    }

    downloadAllAudioSegments() {
        this.splitAudioSegmentsParts.forEach((segment, index) => {
            setTimeout(() => {
                const link = document.createElement('a');
                link.href = segment.download_url;
                link.download = segment.segment;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            }, index * 500);
        });
    }

    resetSplitAudioSegmentsForm() {
        this.splitAudioSegmentsForm.reset();
        document.getElementById('segmentDuration').value = 30;
        this.hideSplitAudioSegmentsProgress();
        this.hideSplitAudioSegmentsAlert();
        this.hideSplitAudioSegmentsResult();
        this.splitAudioSegmentsParts = [];
        this.validateSplitAudioSegmentsForm();
    }

    showSplitAudioSegmentsProgress() {
        this.splitAudioSegmentsProgressContainer.style.display = 'block';
        this.splitAudioSegmentsSubmitBtn.disabled = true;
    }

    hideSplitAudioSegmentsProgress() {
        this.splitAudioSegmentsProgressContainer.style.display = 'none';
        this.splitAudioSegmentsSubmitBtn.disabled = false;
    }

    showSplitAudioSegmentsAlert(type, message) {
        this.splitAudioSegmentsAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideSplitAudioSegmentsAlert() {
        this.splitAudioSegmentsAlertContainer.innerHTML = '';
    }

    showSplitAudioSegmentsResult() {
        this.splitAudioSegmentsResultContainer.style.display = 'block';
    }

    hideSplitAudioSegmentsResult() {
        this.splitAudioSegmentsResultContainer.style.display = 'none';
    }

    // Split Audio by Time Tab Methods
    initializeSplitAudioTimeTab() {
        this.splitAudioTimeForm = document.getElementById('splitAudioTimeForm');
        this.splitAudioTimeSubmitBtn = document.getElementById('splitAudioTimeSubmitBtn');
        this.splitAudioTimeProgressContainer = document.getElementById('splitAudioTimeProgressContainer');
        this.splitAudioTimeAlertContainer = document.getElementById('splitAudioTimeAlertContainer');
        this.splitAudioTimeResultContainer = document.getElementById('splitAudioTimeResultContainer');
        this.splitAudioTimeDownloadBtn = document.getElementById('splitAudioTimeDownloadBtn');
        this.splitAudioTimeResetBtn = document.getElementById('splitAudioTimeResetBtn');

        // Event listeners for Split Audio by Time tab
        this.splitAudioTimeForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSplitAudioTimeSubmit();
        });

        document.getElementById('timeAudioUrl').addEventListener('input', () => {
            this.validateSplitAudioTimeForm();
        });

        document.getElementById('startTimeMs').addEventListener('input', () => {
            this.validateSplitAudioTimeForm();
        });

        document.getElementById('endTimeMs').addEventListener('input', () => {
            this.validateSplitAudioTimeForm();
        });

        this.splitAudioTimeResetBtn.addEventListener('click', () => {
            this.resetSplitAudioTimeForm();
        });

        // Initial validation
        this.validateSplitAudioTimeForm();
    }

    validateSplitAudioTimeForm() {
        const audioUrl = document.getElementById('timeAudioUrl').value;
        const startTime = parseInt(document.getElementById('startTimeMs').value);
        const endTime = parseInt(document.getElementById('endTimeMs').value);
        
        const isValidUrl = this.isValidUrl(audioUrl);
        const isValidTimes = !isNaN(startTime) && !isNaN(endTime) && startTime >= 0 && endTime > startTime;
        
        this.splitAudioTimeSubmitBtn.disabled = !isValidUrl || !isValidTimes;
    }

    async handleSplitAudioTimeSubmit() {
        this.showSplitAudioTimeProgress();
        this.hideSplitAudioTimeAlert();
        this.hideSplitAudioTimeResult();

        const formData = new FormData(this.splitAudioTimeForm);
        const data = {
            audio_url: formData.get('audio_url'),
            start_time: parseInt(formData.get('start_time')),
            end_time: parseInt(formData.get('end_time'))
        };

        try {
            const response = await fetch('/api/split_audio_time', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(data)
            });

            const result = await response.json();

            this.hideSplitAudioTimeProgress();

            if (result.success) {
                this.splitAudioTimeDownloadBtn.href = result.download_url;
                document.getElementById('clipStartTime').textContent = result.start_time_ms;
                document.getElementById('clipEndTime').textContent = result.end_time_ms;
                document.getElementById('clipDuration').textContent = result.duration_ms;
                this.showSplitAudioTimeResult();
                this.showSplitAudioTimeAlert('success', result.message);
            } else {
                this.showSplitAudioTimeAlert('danger', result.error || 'Failed to split audio');
            }
        } catch (error) {
            this.hideSplitAudioTimeProgress();
            this.showSplitAudioTimeAlert('danger', `Error: ${error.message}`);
        }
    }

    resetSplitAudioTimeForm() {
        this.splitAudioTimeForm.reset();
        document.getElementById('startTimeMs').value = '0';
        document.getElementById('endTimeMs').value = '30000';
        this.hideSplitAudioTimeAlert();
        this.hideSplitAudioTimeResult();
        this.validateSplitAudioTimeForm();
    }

    showSplitAudioTimeProgress() {
        this.splitAudioTimeProgressContainer.style.display = 'block';
        this.splitAudioTimeSubmitBtn.disabled = true;
    }

    hideSplitAudioTimeProgress() {
        this.splitAudioTimeProgressContainer.style.display = 'none';
        this.splitAudioTimeSubmitBtn.disabled = false;
    }

    showSplitAudioTimeAlert(type, message) {
        this.splitAudioTimeAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideSplitAudioTimeAlert() {
        this.splitAudioTimeAlertContainer.innerHTML = '';
    }

    showSplitAudioTimeResult() {
        this.splitAudioTimeResultContainer.style.display = 'block';
    }

    hideSplitAudioTimeResult() {
        this.splitAudioTimeResultContainer.style.display = 'none';
    }

    // Trim Audio Tab Methods
    initializeTrimAudioTab() {
        this.trimAudioForm = document.getElementById('trimAudioForm');
        this.trimAudioSubmitBtn = document.getElementById('trimAudioSubmitBtn');
        this.trimAudioProgressContainer = document.getElementById('trimAudioProgressContainer');
        this.trimAudioAlertContainer = document.getElementById('trimAudioAlertContainer');
        this.trimAudioResultContainer = document.getElementById('trimAudioResultContainer');
        this.trimAudioDownloadBtn = document.getElementById('trimAudioDownloadBtn');
        this.trimAudioCleanupBtn = document.getElementById('trimAudioCleanupBtn');
        this.trimAudioResetBtn = document.getElementById('trimAudioResetBtn');
        this.trimAudioCurrentFilename = null;

        // Event listeners for Trim Audio tab
        this.trimAudioForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleTrimAudioSubmit();
        });

        this.trimAudioCleanupBtn.addEventListener('click', () => {
            this.handleTrimAudioCleanup();
        });

        this.trimAudioResetBtn.addEventListener('click', () => {
            this.resetTrimAudioForm();
        });
    }

    async handleTrimAudioSubmit() {
        const formData = new FormData(this.trimAudioForm);
        
        // Convert to JSON
        const jsonData = {};
        formData.forEach((value, key) => {
            jsonData[key] = value;
        });

        this.showTrimAudioProgress();
        this.hideTrimAudioAlert();
        this.hideTrimAudioResult();

        try {
            const response = await fetch('/api/trim_audio', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.trimAudioDownloadBtn.href = result.download_url;
                this.trimAudioDownloadBtn.download = result.filename;
                this.trimAudioCurrentFilename = result.filename;
                
                this.showTrimAudioResult();
                this.showTrimAudioAlert('success', `Audio trimmed to ${result.trimmed_length} seconds successfully!`);
            } else {
                this.showTrimAudioAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Trim audio error:', error);
            this.showTrimAudioAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideTrimAudioProgress();
        }
    }

    async handleTrimAudioCleanup() {
        if (!this.trimAudioCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.trimAudioCurrentFilename}`, {
                method: 'DELETE',
                headers: {
                    'X-API-Key': window.API_KEY
                }
            });

            if (response.ok) {
                this.showTrimAudioAlert('info', 'File deleted from server successfully');
                this.trimAudioCurrentFilename = null;
                this.hideTrimAudioResult();
            } else {
                this.showTrimAudioAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
            }
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showTrimAudioAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
        }
    }

    resetTrimAudioForm() {
        this.trimAudioForm.reset();
        this.hideTrimAudioAlert();
        this.hideTrimAudioResult();
        this.hideTrimAudioProgress();
        this.trimAudioCurrentFilename = null;
    }

    showTrimAudioProgress() {
        this.trimAudioProgressContainer.style.display = 'block';
        this.trimAudioSubmitBtn.disabled = true;
    }

    hideTrimAudioProgress() {
        this.trimAudioProgressContainer.style.display = 'none';
        this.trimAudioSubmitBtn.disabled = false;
    }

    showTrimAudioAlert(type, message) {
        this.trimAudioAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideTrimAudioAlert() {
        this.trimAudioAlertContainer.innerHTML = '';
    }

    showTrimAudioResult() {
        this.trimAudioResultContainer.style.display = 'block';
    }

    hideTrimAudioResult() {
        this.trimAudioResultContainer.style.display = 'none';
    }

    initializeTrimVideoTab() {
        this.trimVideoForm = document.getElementById('trimVideoForm');
        this.trimVideoSubmitBtn = document.getElementById('trimVideoSubmitBtn');
        this.trimVideoProgressContainer = document.getElementById('trimVideoProgressContainer');
        this.trimVideoAlertContainer = document.getElementById('trimVideoAlertContainer');
        this.trimVideoResultContainer = document.getElementById('trimVideoResultContainer');
        this.trimVideoDownloadBtn = document.getElementById('trimVideoDownloadBtn');
        this.trimVideoCleanupBtn = document.getElementById('trimVideoCleanupBtn');
        this.trimVideoResetBtn = document.getElementById('trimVideoResetBtn');
        this.trimVideoCurrentFilename = null;

        this.trimVideoForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleTrimVideoSubmit();
        });

        this.trimVideoCleanupBtn.addEventListener('click', () => {
            this.handleTrimVideoCleanup();
        });

        this.trimVideoResetBtn.addEventListener('click', () => {
            this.resetTrimVideoForm();
        });
    }

    async handleTrimVideoSubmit() {
        const formData = new FormData(this.trimVideoForm);

        const jsonData = {};
        formData.forEach((value, key) => {
            jsonData[key] = value;
        });

        this.showTrimVideoProgress();
        this.hideTrimVideoAlert();
        this.hideTrimVideoResult();

        try {
            const response = await fetch('/api/trim_video', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.trimVideoDownloadBtn.href = result.download_url;
                this.trimVideoDownloadBtn.download = result.filename;
                this.trimVideoCurrentFilename = result.filename;

                this.showTrimVideoResult();
                this.showTrimVideoAlert('success', `Video trimmed from ${result.start_time}s to ${result.end_time}s (${result.duration}s duration) successfully!`);
            } else {
                this.showTrimVideoAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Trim video error:', error);
            this.showTrimVideoAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideTrimVideoProgress();
        }
    }

    async handleTrimVideoCleanup() {
        if (!this.trimVideoCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.trimVideoCurrentFilename}`, {
                method: 'DELETE',
                headers: {
                    'X-API-Key': window.API_KEY
                }
            });

            if (response.ok) {
                this.showTrimVideoAlert('info', 'File deleted from server successfully');
                this.trimVideoCurrentFilename = null;
                this.hideTrimVideoResult();
            } else {
                this.showTrimVideoAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
            }
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showTrimVideoAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
        }
    }

    resetTrimVideoForm() {
        this.trimVideoForm.reset();
        this.hideTrimVideoAlert();
        this.hideTrimVideoResult();
        this.hideTrimVideoProgress();
        this.trimVideoCurrentFilename = null;
    }

    showTrimVideoProgress() {
        this.trimVideoProgressContainer.style.display = 'block';
        this.trimVideoSubmitBtn.disabled = true;
    }

    hideTrimVideoProgress() {
        this.trimVideoProgressContainer.style.display = 'none';
        this.trimVideoSubmitBtn.disabled = false;
    }

    showTrimVideoAlert(type, message) {
        this.trimVideoAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideTrimVideoAlert() {
        this.trimVideoAlertContainer.innerHTML = '';
    }

    showTrimVideoResult() {
        this.trimVideoResultContainer.style.display = 'block';
    }

    hideTrimVideoResult() {
        this.trimVideoResultContainer.style.display = 'none';
    }

    initializeSplitVideoTab() {
        this.splitVideoForm = document.getElementById('splitVideoForm');
        this.splitVideoSubmitBtn = document.getElementById('splitVideoSubmitBtn');
        this.splitVideoProgressContainer = document.getElementById('splitVideoProgressContainer');
        this.splitVideoAlertContainer = document.getElementById('splitVideoAlertContainer');
        this.splitVideoResultContainer = document.getElementById('splitVideoResultContainer');
        this.splitVideoDownloadPart1Btn = document.getElementById('splitVideoDownloadPart1Btn');
        this.splitVideoDownloadPart2Btn = document.getElementById('splitVideoDownloadPart2Btn');
        this.splitVideoCleanupBtn = document.getElementById('splitVideoCleanupBtn');
        this.splitVideoResetBtn = document.getElementById('splitVideoResetBtn');
        this.splitVideoPart1Filename = null;
        this.splitVideoPart2Filename = null;

        this.splitVideoForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleSplitVideoSubmit();
        });

        this.splitVideoCleanupBtn.addEventListener('click', () => {
            this.handleSplitVideoCleanup();
        });

        this.splitVideoResetBtn.addEventListener('click', () => {
            this.resetSplitVideoForm();
        });
    }

    async handleSplitVideoSubmit() {
        const formData = new FormData(this.splitVideoForm);
        const jsonData = { video_url: formData.get('video_url') };
        const splitAt = formData.get('split_at_seconds');
        if (splitAt !== null && String(splitAt).trim() !== '') {
            const num = parseFloat(splitAt);
            if (!isNaN(num)) jsonData.split_at_seconds = num;
        }

        this.showSplitVideoProgress();
        this.hideSplitVideoAlert();
        this.hideSplitVideoResult();

        try {
            const response = await fetch('/api/split_video', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.splitVideoDownloadPart1Btn.href = result.part1_url;
                this.splitVideoDownloadPart1Btn.download = result.part1_filename;
                this.splitVideoDownloadPart2Btn.href = result.part2_url;
                this.splitVideoDownloadPart2Btn.download = result.part2_filename;
                this.splitVideoPart1Filename = result.part1_filename;
                this.splitVideoPart2Filename = result.part2_filename;

                this.showSplitVideoResult();
                this.showSplitVideoAlert('success', `Video split at ${result.split_at_seconds}s (duration ${result.duration_seconds}s). Part 1 and Part 2 ready.`);
            } else {
                this.showSplitVideoAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Split video error:', error);
            this.showSplitVideoAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideSplitVideoProgress();
        }
    }

    async handleSplitVideoCleanup() {
        if (!this.splitVideoPart1Filename && !this.splitVideoPart2Filename) return;

        try {
            if (this.splitVideoPart1Filename) {
                await fetch(`/api/cleanup/${this.splitVideoPart1Filename}`, {
                    method: 'DELETE',
                    headers: { 'X-API-Key': window.API_KEY }
                });
            }
            if (this.splitVideoPart2Filename) {
                await fetch(`/api/cleanup/${this.splitVideoPart2Filename}`, {
                    method: 'DELETE',
                    headers: { 'X-API-Key': window.API_KEY }
                });
            }
            this.showSplitVideoAlert('info', 'Files deleted from server successfully');
            this.splitVideoPart1Filename = null;
            this.splitVideoPart2Filename = null;
            this.hideSplitVideoResult();
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showSplitVideoAlert('warning', 'File cleanup failed, but files will be automatically deleted after 24 hours');
        }
    }

    resetSplitVideoForm() {
        this.splitVideoForm.reset();
        this.hideSplitVideoAlert();
        this.hideSplitVideoResult();
        this.hideSplitVideoProgress();
        this.splitVideoPart1Filename = null;
        this.splitVideoPart2Filename = null;
    }

    showSplitVideoProgress() {
        this.splitVideoProgressContainer.style.display = 'block';
        this.splitVideoSubmitBtn.disabled = true;
    }

    hideSplitVideoProgress() {
        this.splitVideoProgressContainer.style.display = 'none';
        this.splitVideoSubmitBtn.disabled = false;
    }

    showSplitVideoAlert(type, message) {
        this.splitVideoAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideSplitVideoAlert() {
        this.splitVideoAlertContainer.innerHTML = '';
    }

    showSplitVideoResult() {
        this.splitVideoResultContainer.style.display = 'block';
    }

    hideSplitVideoResult() {
        this.splitVideoResultContainer.style.display = 'none';
    }

    initializeFirstFrameTab() {
        this.firstFrameForm = document.getElementById('firstFrameForm');
        this.firstFrameSubmitBtn = document.getElementById('firstFrameSubmitBtn');
        this.firstFrameProgressContainer = document.getElementById('firstFrameProgressContainer');
        this.firstFrameAlertContainer = document.getElementById('firstFrameAlertContainer');
        this.firstFrameResultContainer = document.getElementById('firstFrameResultContainer');
        this.firstFrameImagePreview = document.getElementById('firstFrameImagePreview');
        this.firstFrameImageLink = document.getElementById('firstFrameImageLink');
        this.firstFrameResetBtn = document.getElementById('firstFrameResetBtn');

        this.firstFrameForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleFirstFrameSubmit();
        });

        this.firstFrameResetBtn.addEventListener('click', () => {
            this.resetFirstFrameForm();
        });
    }

    async handleFirstFrameSubmit() {
        const formData = new FormData(this.firstFrameForm);
        const jsonData = { video_url: formData.get('video_url') };

        this.showFirstFrameProgress();
        this.hideFirstFrameAlert();
        this.hideFirstFrameResult();

        try {
            const response = await fetch('/api/get_first_frame_image', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                const imageUrl = result.image_url || result.download_url;
                this.firstFrameImagePreview.src = imageUrl;
                this.firstFrameImageLink.href = imageUrl;
                this.showFirstFrameResult();
                this.showFirstFrameAlert('success', 'First frame extracted successfully.');
            } else {
                this.showFirstFrameAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('First frame error:', error);
            this.showFirstFrameAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideFirstFrameProgress();
        }
    }

    resetFirstFrameForm() {
        this.firstFrameForm.reset();
        this.hideFirstFrameAlert();
        this.hideFirstFrameResult();
        this.firstFrameImagePreview.src = '';
        this.firstFrameImageLink.href = '#';
    }

    showFirstFrameProgress() {
        this.firstFrameProgressContainer.style.display = 'block';
        this.firstFrameSubmitBtn.disabled = true;
    }

    hideFirstFrameProgress() {
        this.firstFrameProgressContainer.style.display = 'none';
        this.firstFrameSubmitBtn.disabled = false;
    }

    showFirstFrameAlert(type, message) {
        this.firstFrameAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideFirstFrameAlert() {
        this.firstFrameAlertContainer.innerHTML = '';
    }

    showFirstFrameResult() {
        this.firstFrameResultContainer.style.display = 'block';
    }

    hideFirstFrameResult() {
        this.firstFrameResultContainer.style.display = 'none';
    }

    initializeLastFrameTab() {
        this.lastFrameForm = document.getElementById('lastFrameForm');
        this.lastFrameSubmitBtn = document.getElementById('lastFrameSubmitBtn');
        this.lastFrameProgressContainer = document.getElementById('lastFrameProgressContainer');
        this.lastFrameAlertContainer = document.getElementById('lastFrameAlertContainer');
        this.lastFrameResultContainer = document.getElementById('lastFrameResultContainer');
        this.lastFrameImagePreview = document.getElementById('lastFrameImagePreview');
        this.lastFrameImageLink = document.getElementById('lastFrameImageLink');
        this.lastFrameResetBtn = document.getElementById('lastFrameResetBtn');

        this.lastFrameForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleLastFrameSubmit();
        });

        this.lastFrameResetBtn.addEventListener('click', () => {
            this.resetLastFrameForm();
        });
    }

    async handleLastFrameSubmit() {
        const formData = new FormData(this.lastFrameForm);
        const jsonData = { video_url: formData.get('video_url') };

        this.showLastFrameProgress();
        this.hideLastFrameAlert();
        this.hideLastFrameResult();

        try {
            const response = await fetch('/api/get_last_frame_image', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                const imageUrl = result.image_url || result.download_url;
                this.lastFrameImagePreview.src = imageUrl;
                this.lastFrameImageLink.href = imageUrl;
                this.showLastFrameResult();
                this.showLastFrameAlert('success', 'Last frame extracted successfully.');
            } else {
                this.showLastFrameAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Last frame error:', error);
            this.showLastFrameAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideLastFrameProgress();
        }
    }

    resetLastFrameForm() {
        this.lastFrameForm.reset();
        this.hideLastFrameAlert();
        this.hideLastFrameResult();
        this.lastFrameImagePreview.src = '';
        this.lastFrameImageLink.href = '#';
    }

    showLastFrameProgress() {
        this.lastFrameProgressContainer.style.display = 'block';
        this.lastFrameSubmitBtn.disabled = true;
    }

    hideLastFrameProgress() {
        this.lastFrameProgressContainer.style.display = 'none';
        this.lastFrameSubmitBtn.disabled = false;
    }

    showLastFrameAlert(type, message) {
        this.lastFrameAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideLastFrameAlert() {
        this.lastFrameAlertContainer.innerHTML = '';
    }

    showLastFrameResult() {
        this.lastFrameResultContainer.style.display = 'block';
    }

    hideLastFrameResult() {
        this.lastFrameResultContainer.style.display = 'none';
    }

    initializeConvertVerticalTab() {
        this.convertVerticalForm = document.getElementById('convertVerticalForm');
        this.convertVerticalSubmitBtn = document.getElementById('convertVerticalSubmitBtn');
        this.convertVerticalProgressContainer = document.getElementById('convertVerticalProgressContainer');
        this.convertVerticalAlertContainer = document.getElementById('convertVerticalAlertContainer');
        this.convertVerticalResultContainer = document.getElementById('convertVerticalResultContainer');
        this.convertVerticalDownloadBtn = document.getElementById('convertVerticalDownloadBtn');
        this.convertVerticalCleanupBtn = document.getElementById('convertVerticalCleanupBtn');
        this.convertVerticalResetBtn = document.getElementById('convertVerticalResetBtn');
        this.convertVerticalCurrentFilename = null;

        // Event listeners for Convert to Vertical tab
        this.convertVerticalForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleConvertVerticalSubmit();
        });

        this.convertVerticalCleanupBtn.addEventListener('click', () => {
            this.handleConvertVerticalCleanup();
        });

        this.convertVerticalResetBtn.addEventListener('click', () => {
            this.resetConvertVerticalForm();
        });
    }

    async handleConvertVerticalSubmit() {
        const formData = new FormData(this.convertVerticalForm);
        
        // Convert to JSON
        const jsonData = {};
        formData.forEach((value, key) => {
            if (value) {  // Only include non-empty values
                jsonData[key] = value;
            }
        });

        this.showConvertVerticalProgress();
        this.hideConvertVerticalAlert();
        this.hideConvertVerticalResult();

        try {
            const response = await fetch('/api/convert_to_vertical', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.convertVerticalDownloadBtn.href = result.download_url;
                this.convertVerticalDownloadBtn.download = result.filename;
                this.convertVerticalCurrentFilename = result.filename;
                
                // Update result message with aspect ratio info
                const messageElement = document.getElementById('convertVerticalResultMessage');
                messageElement.textContent = result.message || 'Your video has been converted to vertical format and is ready for download.';
                
                this.showConvertVerticalResult();
                this.showConvertVerticalAlert('success', result.message || 'Video converted successfully!');
            } else {
                this.showConvertVerticalAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Convert to vertical error:', error);
            this.showConvertVerticalAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideConvertVerticalProgress();
        }
    }

    async handleConvertVerticalCleanup() {
        if (!this.convertVerticalCurrentFilename) return;

        try {
            const response = await fetch(`/api/cleanup/${this.convertVerticalCurrentFilename}`, {
                method: 'DELETE',
                headers: {
                    'X-API-Key': window.API_KEY
                }
            });

            if (response.ok) {
                this.showConvertVerticalAlert('info', 'File deleted from server successfully');
                this.convertVerticalCurrentFilename = null;
                this.hideConvertVerticalResult();
            } else {
                this.showConvertVerticalAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
            }
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showConvertVerticalAlert('warning', 'File cleanup failed, but it will be automatically deleted after 24 hours');
        }
    }

    resetConvertVerticalForm() {
        this.convertVerticalForm.reset();
        this.hideConvertVerticalAlert();
        this.hideConvertVerticalResult();
        this.hideConvertVerticalProgress();
        this.convertVerticalCurrentFilename = null;
    }

    showConvertVerticalProgress() {
        this.convertVerticalProgressContainer.style.display = 'block';
        this.convertVerticalSubmitBtn.disabled = true;
    }

    hideConvertVerticalProgress() {
        this.convertVerticalProgressContainer.style.display = 'none';
        this.convertVerticalSubmitBtn.disabled = false;
    }

    showConvertVerticalAlert(type, message) {
        this.convertVerticalAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideConvertVerticalAlert() {
        this.convertVerticalAlertContainer.innerHTML = '';
    }

    showConvertVerticalResult() {
        this.convertVerticalResultContainer.style.display = 'block';
    }

    hideConvertVerticalResult() {
        this.convertVerticalResultContainer.style.display = 'none';
    }

    initializeAutoCaptionTab() {
        this.autoCaptionForm = document.getElementById('autoCaptionForm');
        this.autoCaptionSubmitBtn = document.getElementById('autoCaptionSubmitBtn');
        this.autoCaptionProgressContainer = document.getElementById('autoCaptionProgressContainer');
        this.autoCaptionProgressText = document.getElementById('autoCaptionProgressText');
        this.autoCaptionAlertContainer = document.getElementById('autoCaptionAlertContainer');
        this.autoCaptionResultContainer = document.getElementById('autoCaptionResultContainer');
        this.autoCaptionDownloadVideoBtn = document.getElementById('autoCaptionDownloadVideoBtn');
        this.autoCaptionDownloadJsonBtn = document.getElementById('autoCaptionDownloadJsonBtn');
        this.autoCaptionDownloadSrtBtn = document.getElementById('autoCaptionDownloadSrtBtn');
        this.autoCaptionDownloadVttBtn = document.getElementById('autoCaptionDownloadVttBtn');
        this.autoCaptionResetBtn = document.getElementById('autoCaptionResetBtn');

        this.autoCaptionForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleAutoCaptionSubmit();
        });

        this.autoCaptionResetBtn.addEventListener('click', () => {
            this.resetAutoCaptionForm();
        });
    }

    async handleAutoCaptionSubmit() {
        const jsonData = {
            video_url: document.getElementById('autoCaptionVideoUrl').value,
            subtitle_style: document.getElementById('autoCaptionStyle').value,
            language: document.getElementById('autoCaptionLanguage').value,
            aspect_ratio: document.getElementById('autoCaptionAspectRatio').value,
            position: document.getElementById('autoCaptionPosition').value,
            max_chars_per_line: parseInt(document.getElementById('autoCaptionMaxChars').value, 10) || 20,
            max_lines: parseInt(document.getElementById('autoCaptionMaxLines').value, 10) || 1,
        };

        this.showAutoCaptionProgress();
        this.hideAutoCaptionAlert();
        this.hideAutoCaptionResult();

        try {
            const response = await fetch('/api/videos/add-tiktok-captions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.autoCaptionDownloadVideoBtn.href = result.download_url;
                if (result.captions_json_url) {
                    this.autoCaptionDownloadJsonBtn.href = result.captions_json_url;
                    this.autoCaptionDownloadJsonBtn.style.display = '';
                } else {
                    this.autoCaptionDownloadJsonBtn.style.display = 'none';
                }
                if (result.srt_url) {
                    this.autoCaptionDownloadSrtBtn.href = result.srt_url;
                    this.autoCaptionDownloadSrtBtn.style.display = '';
                } else {
                    this.autoCaptionDownloadSrtBtn.style.display = 'none';
                }
                if (result.vtt_url) {
                    this.autoCaptionDownloadVttBtn.href = result.vtt_url;
                    this.autoCaptionDownloadVttBtn.style.display = '';
                } else {
                    this.autoCaptionDownloadVttBtn.style.display = 'none';
                }

                const messageElement = document.getElementById('autoCaptionResultMessage');
                const wordInfo = result.word_count ? ` (${result.word_count} words detected)` : '';
                messageElement.textContent = (result.message || 'AI captions generated successfully!') + wordInfo;

                this.showAutoCaptionResult();
                this.showAutoCaptionAlert('success', result.message || 'AI captions generated successfully!');
            } else {
                this.showAutoCaptionAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('AI captions error:', error);
            this.showAutoCaptionAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideAutoCaptionProgress();
        }
    }

    resetAutoCaptionForm() {
        this.autoCaptionForm.reset();
        this.hideAutoCaptionAlert();
        this.hideAutoCaptionResult();
        this.hideAutoCaptionProgress();
    }

    showAutoCaptionProgress() {
        this.autoCaptionProgressContainer.style.display = 'block';
        this.autoCaptionSubmitBtn.disabled = true;
    }

    hideAutoCaptionProgress() {
        this.autoCaptionProgressContainer.style.display = 'none';
        this.autoCaptionSubmitBtn.disabled = false;
    }

    showAutoCaptionAlert(type, message) {
        this.autoCaptionAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideAutoCaptionAlert() {
        this.autoCaptionAlertContainer.innerHTML = '';
    }

    showAutoCaptionResult() {
        this.autoCaptionResultContainer.style.display = 'block';
    }

    hideAutoCaptionResult() {
        this.autoCaptionResultContainer.style.display = 'none';
    }

    initializeTextOverlayTab() {
        this.textOverlayForm = document.getElementById('textOverlayForm');
        this.textOverlaySubmitBtn = document.getElementById('textOverlaySubmitBtn');
        this.textOverlayProgressContainer = document.getElementById('textOverlayProgressContainer');
        this.textOverlayAlertContainer = document.getElementById('textOverlayAlertContainer');
        this.textOverlayResultContainer = document.getElementById('textOverlayResultContainer');
        this.textOverlayDownloadVideoBtn = document.getElementById('textOverlayDownloadVideoBtn');
        this.textOverlayResetBtn = document.getElementById('textOverlayResetBtn');

        this.textOverlayForm.addEventListener('submit', (e) => {
            e.preventDefault();
            this.handleTextOverlaySubmit();
        });

        this.textOverlayResetBtn.addEventListener('click', () => {
            this.resetTextOverlayForm();
        });
    }

    async handleTextOverlaySubmit() {
        const jsonData = {
            video_url: document.getElementById('textOverlayVideoUrl').value,
            text: document.getElementById('textOverlayText').value,
            subtitle_style: document.getElementById('textOverlayStyle').value,
            aspect_ratio: document.getElementById('textOverlayAspectRatio').value,
            position: document.getElementById('textOverlayPosition').value,
            duration_per_line: parseInt(document.getElementById('textOverlayDuration').value, 10) || 5,
        };

        this.showTextOverlayProgress();
        this.hideTextOverlayAlert();
        this.hideTextOverlayResult();

        try {
            const response = await fetch('/api/videos/add-text-overlay-captions', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-API-Key': window.API_KEY
                },
                body: JSON.stringify(jsonData)
            });

            const result = await response.json();

            if (result.success) {
                this.textOverlayDownloadVideoBtn.href = result.download_url;

                const messageElement = document.getElementById('textOverlayResultMessage');
                const lineInfo = result.line_count ? ` (${result.line_count} lines, ${result.total_duration_seconds}s total)` : '';
                messageElement.textContent = (result.message || 'Text overlay captions generated successfully!') + lineInfo;

                this.showTextOverlayResult();
                this.showTextOverlayAlert('success', result.message || 'Text overlay captions generated successfully!');
            } else {
                this.showTextOverlayAlert('danger', `Error: ${result.error}`);
            }
        } catch (error) {
            console.error('Text overlay error:', error);
            this.showTextOverlayAlert('danger', `An error occurred: ${error.message}`);
        } finally {
            this.hideTextOverlayProgress();
        }
    }

    resetTextOverlayForm() {
        this.textOverlayForm.reset();
        this.hideTextOverlayAlert();
        this.hideTextOverlayResult();
        this.hideTextOverlayProgress();
    }

    showTextOverlayProgress() {
        this.textOverlayProgressContainer.style.display = 'block';
        this.textOverlaySubmitBtn.disabled = true;
    }

    hideTextOverlayProgress() {
        this.textOverlayProgressContainer.style.display = 'none';
        this.textOverlaySubmitBtn.disabled = false;
    }

    showTextOverlayAlert(type, message) {
        this.textOverlayAlertContainer.innerHTML = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                <i class="fas fa-${this.getAlertIcon(type)} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
    }

    hideTextOverlayAlert() {
        this.textOverlayAlertContainer.innerHTML = '';
    }

    showTextOverlayResult() {
        this.textOverlayResultContainer.style.display = 'block';
    }

    hideTextOverlayResult() {
        this.textOverlayResultContainer.style.display = 'none';
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
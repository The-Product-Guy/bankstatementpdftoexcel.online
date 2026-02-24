// DOM Elements
const fileInput = document.getElementById('pdf_file');
const fileUploadArea = document.getElementById('fileUploadArea');
const fileInfo = document.getElementById('fileInfo');
const fileName = document.getElementById('fileName');
const fileSize = document.getElementById('fileSize');
const removeFileBtn = document.getElementById('removeFile');
const convertBtn = document.getElementById('convertBtn');
const uploadForm = document.getElementById('uploadForm');
const progressModal = document.getElementById('progressModal');
const progressFill = document.getElementById('progressFill');
const progressInfo = document.getElementById('progressInfo');
const progressBar = document.getElementById('progressBar');
const limitModal = document.getElementById('limitModal');
const limitTitle = document.getElementById('limitTitle');
const limitMessage = document.getElementById('limitMessage');
const limitClose = document.getElementById('limitClose');
const qualitySelector = document.getElementById('qualitySelector');

// Initialize WebSocket connection
let socket = null;

// Initialize page
document.addEventListener('DOMContentLoaded', function () {
    checkFormValidity();
    setupEventListeners();
    initializeWebSocket();
});

// Initialize WebSocket connection
function initializeWebSocket() {
    socket = io();

    socket.on('connect', function () {
        console.log('Connected to server');
    });

    socket.on('progress_update', function (data) {
        updateProgress(data);
    });

    socket.on('disconnect', function () {
        console.log('Disconnected from server');
    });
}

// Setup event listeners
function setupEventListeners() {
    // File input change
    fileInput.addEventListener('change', handleFileSelect);

    // Drag and drop handlers
    fileUploadArea.addEventListener('dragover', handleDragOver);
    fileUploadArea.addEventListener('dragleave', handleDragLeave);
    fileUploadArea.addEventListener('drop', handleFileDrop);

    // Remove file button
    removeFileBtn.addEventListener('click', clearFileSelection);

    // Form submission
    uploadForm.addEventListener('submit', handleFormSubmit);

    if (limitClose) {
        limitClose.addEventListener('click', hideLimitModal);
    }

    // Quality selector toggle
    if (qualitySelector) {
        const qualityOptions = qualitySelector.querySelectorAll('.quality-option');
        qualityOptions.forEach(option => {
            option.addEventListener('click', function () {
                qualityOptions.forEach(opt => opt.classList.remove('selected'));
                this.classList.add('selected');
                this.querySelector('input[type="radio"]').checked = true;
            });
        });
    }

    // Result banner close
    const resultClose = document.getElementById('resultClose');
    if (resultClose) {
        resultClose.addEventListener('click', () => {
            document.getElementById('resultBanner').style.display = 'none';
        });
    }

    // Feedback modal
    const feedbackCloseBtn = document.getElementById('feedbackClose');
    if (feedbackCloseBtn) {
        feedbackCloseBtn.addEventListener('click', closeFeedbackModal);
    }
    const feedbackForm = document.getElementById('feedbackForm');
    if (feedbackForm) {
        feedbackForm.addEventListener('submit', submitFeedback);
    }

    // Prevent default drag behaviors on document
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        document.addEventListener(eventName, preventDefaults, false);
    });
}

// Prevent default drag behaviors
function preventDefaults(e) {
    e.preventDefault();
    e.stopPropagation();
}

// Handle drag over
function handleDragOver(e) {
    e.preventDefault();
    fileUploadArea.classList.add('dragover');
}

// Handle drag leave
function handleDragLeave(e) {
    e.preventDefault();
    fileUploadArea.classList.remove('dragover');
}

// Handle file drop
function handleFileDrop(e) {
    e.preventDefault();
    fileUploadArea.classList.remove('dragover');

    const files = e.dataTransfer.files;
    if (files.length > 0) {
        const file = files[0];
        if (validateFile(file)) {
            displayFileInfo(file);
            // Set the file to the input element
            const dt = new DataTransfer();
            dt.items.add(file);
            fileInput.files = dt.files;
            checkFormValidity();
        }
    }
}

// Handle file selection
function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) {
        if (validateFile(file)) {
            displayFileInfo(file);
            checkFormValidity();
        } else {
            clearFileSelection();
        }
    }
}

// Validate file
function validateFile(file) {
    const fileName = (file.name || '').toLowerCase();
    const isPdfType = file.type === 'application/pdf' ||
        file.type === 'application/x-pdf' ||
        file.type === 'application/octet-stream' ||
        fileName.endsWith('.pdf');

    // Check file type
    if (!isPdfType) {
        showAlert('Please select a PDF file.', 'error');
        return false;
    }

    // Check file size (100MB limit)
    const maxSizeMb = parseInt(fileInput.dataset.maxSize || '100', 10);
    const maxSize = maxSizeMb * 1024 * 1024;
    if (file.size > maxSize) {
        showLimitModal('FILE_TOO_LARGE', maxSizeMb);
        return false;
    }

    return true;
}

function showLimitModal(type, limitValue, pageCount) {
    if (!limitModal) {
        return;
    }

    if (type === 'FILE_TOO_LARGE') {
        limitTitle.textContent = 'File too large';
        limitMessage.textContent = `This file exceeds the ${limitValue}MB limit. Split the PDF and try again.`;
    } else if (type === 'PAGE_LIMIT_EXCEEDED') {
        const pageText = pageCount ? ` (${pageCount} pages)` : '';
        limitTitle.textContent = 'Too many pages';
        limitMessage.textContent = `This PDF has more than ${limitValue} pages${pageText}. Split the PDF and try again.`;
    } else {
        limitTitle.textContent = 'File limit reached';
        limitMessage.textContent = 'Your file exceeds the allowed limit. Split the PDF and try again.';
    }

    limitModal.style.display = 'flex';
    limitModal.setAttribute('aria-hidden', 'false');
    limitModal.focus();
}

function hideLimitModal() {
    if (!limitModal) {
        return;
    }
    limitModal.style.display = 'none';
    limitModal.setAttribute('aria-hidden', 'true');
}

// Display file information
function displayFileInfo(file) {
    fileName.textContent = file.name;
    fileSize.textContent = formatFileSize(file.size);

    // Hide upload area, show file info and quality selector
    fileUploadArea.style.display = 'none';
    fileInfo.style.display = 'flex';
    if (qualitySelector) {
        qualitySelector.style.display = 'block';
    }
}

// Clear file selection
function clearFileSelection() {
    fileInput.value = '';
    fileUploadArea.style.display = 'block';
    fileInfo.style.display = 'none';
    if (qualitySelector) {
        qualitySelector.style.display = 'none';
        // Reset quality to standard
        const standardOption = qualitySelector.querySelector('[data-quality="standard"]');
        const highOption = qualitySelector.querySelector('[data-quality="high"]');
        if (standardOption && highOption) {
            standardOption.classList.add('selected');
            highOption.classList.remove('selected');
            standardOption.querySelector('input[type="radio"]').checked = true;
        }
    }
    checkFormValidity();
}

// Format file size
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';

    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Check form validity and enable/disable convert button
function checkFormValidity() {
    const fileSelected = fileInput.files.length > 0;

    convertBtn.disabled = !fileSelected;
}

// Handle form submission
function handleFormSubmit(e) {
    e.preventDefault();
    // Show loading state on button
    const btnText = convertBtn.querySelector('.btn-text');
    const loadingSpinner = convertBtn.querySelector('.loading-spinner');

    if (btnText && loadingSpinner) {
        btnText.style.display = 'none';
        loadingSpinner.style.display = 'flex';
    }
    convertBtn.disabled = true;

    showProgressModal();
    submitFormWithProgress();
}

// Show progress modal
function showProgressModal() {
    progressModal.style.display = 'flex';
    progressModal.setAttribute('aria-hidden', 'false');
    progressModal.setAttribute('aria-busy', 'true');
    progressModal.focus();
    resetProgressSteps();
}

// Hide progress modal and reset form
function hideProgressModal() {
    progressModal.style.display = 'none';
    progressModal.setAttribute('aria-hidden', 'true');
    progressModal.setAttribute('aria-busy', 'false');
    resetForm();
}

// Reset progress steps
function resetProgressSteps() {
    // Reset progress bar to initial state
    progressFill.style.width = '0%';
    progressInfo.textContent = 'Starting conversion...';
    if (progressBar) {
        progressBar.setAttribute('aria-valuenow', '0');
    }

    const steps = document.querySelectorAll('.progress-step');
    steps.forEach((step, index) => {
        step.classList.remove('active', 'completed');
        if (index === 0) {
            step.classList.add('active');
            step.style.opacity = '1';
        } else {
            step.style.opacity = '0.5';
        }

        // Reset status icons
        const statusIcon = step.querySelector('.step-status i');
        if (index === 0) {
            statusIcon.className = 'fas fa-spinner fa-spin';
        } else {
            statusIcon.className = 'fas fa-clock';
        }
    });
}

// Update progress based on real-time data from server
function updateProgress(data) {
    const currentPage = data.current_page ?? data.current ?? 0;
    const totalPages = data.total_pages ?? data.total ?? 0;
    const status = data.status || 'Processing...';
    const percentage = data.percent ?? data.percentage ?? 0;

    // Update progress bar
    progressFill.style.width = percentage + '%';
    if (progressBar) {
        progressBar.setAttribute('aria-valuenow', `${percentage}`);
    }

    // Update progress info text
    if (currentPage > 0 && totalPages > 0) {
        progressInfo.textContent = `${status} - Page ${currentPage} of ${totalPages} (${percentage}%)`;
    } else {
        progressInfo.textContent = status;
    }

    // Update step indicators based on progress
    const steps = document.querySelectorAll('.progress-step');

    if (percentage <= 25) {
        // Step 1: Loading PDF
        updateStepStatus(steps[0], 'active');
        updateStepStatus(steps[1], 'pending');
        updateStepStatus(steps[2], 'pending');
        updateStepStatus(steps[3], 'pending');
    } else if (percentage <= 75) {
        // Step 2: Processing pages
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'active');
        updateStepStatus(steps[2], 'pending');
        updateStepStatus(steps[3], 'pending');
    } else if (percentage < 100) {
        // Step 3: Extracting data
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'completed');
        updateStepStatus(steps[2], 'active');
        updateStepStatus(steps[3], 'pending');
    } else {
        // Step 4: Generating Excel
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'completed');
        updateStepStatus(steps[2], 'completed');
        updateStepStatus(steps[3], 'active');
    }
}

// Update individual step status
function updateStepStatus(step, status) {
    const statusIcon = step.querySelector('.step-status i');

    step.classList.remove('active', 'completed');
    step.style.opacity = status === 'pending' ? '0.5' : '1';

    if (status === 'active') {
        step.classList.add('active');
        statusIcon.className = 'fas fa-spinner fa-spin';
    } else if (status === 'completed') {
        step.classList.add('completed');
        statusIcon.className = 'fas fa-check';
    } else {
        statusIcon.className = 'fas fa-clock';
    }
}

let activeJobId = null;
let downloadTriggered = false;
let completionPolls = 0;
let lastJobData = null; // Store last job result metadata for feedback

function startStatusPolling(jobId) {
    activeJobId = jobId;
    downloadTriggered = false;
    completionPolls = 0;
    lastJobData = null;
    pollJobStatus();
}

function pollJobStatus() {
    if (!activeJobId) {
        return;
    }

    fetch(`/status/${activeJobId}`)
        .then(response => response.json())
        .then(data => {
            updateProgress(data);

            const status = data.status || '';

            if (status.startsWith('Error')) {
                activeJobId = null;
                hideProgressModal();
                showAlert(status, 'error');
                return;
            }

            if (data.download_url && !downloadTriggered) {
                const jobId = activeJobId;
                activeJobId = null;
                downloadTriggered = true;
                lastJobData = data;
                triggerDownload(data.download_url);
                hideProgressModal();
                showResultBanner(data, jobId);
                return;
            }

            // Handle "completed but no data" case
            if (status.startsWith('Completed') && !data.download_url) {
                completionPolls += 1;
                // Check if it's a "no data" completion with metadata
                const confidence = data.confidence || '';
                if (confidence === 'empty' || confidence === 'low' || completionPolls >= 5) {
                    const jobId = activeJobId;
                    activeJobId = null;
                    lastJobData = data;
                    hideProgressModal();
                    showResultBanner(data, jobId);
                    return;
                }
            }

            setTimeout(pollJobStatus, 1000);
        })
        .catch(() => {
            setTimeout(pollJobStatus, 2000);
        });
}

function triggerDownload(url) {
    const a = document.createElement('a');
    a.href = url;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ===== Result Banner =====

function showResultBanner(data, jobId) {
    const banner = document.getElementById('resultBanner');
    const icon = document.getElementById('resultIcon');
    const title = document.getElementById('resultTitle');
    const message = document.getElementById('resultMessage');
    const meta = document.getElementById('resultMeta');
    const actions = document.getElementById('resultActions');

    if (!banner) return;

    const confidence = data.confidence || 'good';
    const rows = data.extraction_rows || 0;
    const cols = data.extraction_cols || 0;
    const qualityUsed = data.quality_used || 'standard';
    const qualityMsg = data.quality_message || '';
    const docHint = data.document_hint || 'statement';

    // Clear previous state
    banner.className = 'result-banner';
    actions.innerHTML = '';
    meta.textContent = '';

    if (confidence === 'good' && rows > 0) {
        // Success
        banner.classList.add('success');
        icon.innerHTML = '<i class="fas fa-check-circle"></i>';
        title.textContent = 'Conversion Complete';
        message.textContent = 'Your file has been downloaded successfully.';
        meta.textContent = `${rows} rows \u00d7 ${cols} columns extracted`;

        // Still offer feedback in case data isn't perfect
        const fbBtn = document.createElement('button');
        fbBtn.className = 'btn-feedback';
        fbBtn.textContent = 'Report an issue';
        fbBtn.onclick = () => openFeedbackModal(jobId, data);
        actions.appendChild(fbBtn);

    } else if (confidence === 'low') {
        // Low confidence - offer retry + feedback
        banner.classList.add('warning');
        icon.innerHTML = '<i class="fas fa-exclamation-triangle"></i>';
        title.textContent = 'Partial Extraction';
        message.textContent = qualityMsg || 'Some data may be missing or incomplete.';
        meta.textContent = `${rows} rows extracted \u00b7 ${qualityUsed === 'standard' ? 'Standard' : 'High'} quality`;

        if (qualityUsed === 'standard') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'btn-retry';
            retryBtn.innerHTML = '<i class="fas fa-redo"></i> Retry in High Quality';
            retryBtn.onclick = () => retryWithHighQuality();
            actions.appendChild(retryBtn);
        }

        const fbBtn = document.createElement('button');
        fbBtn.className = 'btn-feedback';
        fbBtn.textContent = 'Submit feedback';
        fbBtn.onclick = () => openFeedbackModal(jobId, data);
        actions.appendChild(fbBtn);

    } else {
        // Empty / non-tabular
        banner.classList.add('error');
        icon.innerHTML = '<i class="fas fa-times-circle"></i>';

        if (docHint === 'non_tabular') {
            title.textContent = 'Not a Bank Statement';
            message.textContent = qualityMsg || 'This PDF does not appear to contain tabular data.';
        } else {
            title.textContent = 'No Data Extracted';
            message.textContent = qualityMsg || 'We could not extract any data from this PDF.';
        }

        if (qualityUsed === 'standard' && docHint !== 'non_tabular') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'btn-retry';
            retryBtn.innerHTML = '<i class="fas fa-redo"></i> Retry in High Quality';
            retryBtn.onclick = () => retryWithHighQuality();
            actions.appendChild(retryBtn);
        }

        const fbBtn = document.createElement('button');
        fbBtn.className = 'btn-feedback';
        fbBtn.textContent = 'Submit feedback';
        fbBtn.onclick = () => openFeedbackModal(jobId, data);
        actions.appendChild(fbBtn);
    }

    banner.style.display = 'block';

    // Auto-hide success banners after 15 seconds
    if (confidence === 'good') {
        setTimeout(() => { banner.style.display = 'none'; }, 15000);
    }
}

function retryWithHighQuality() {
    // Hide result banner
    const banner = document.getElementById('resultBanner');
    if (banner) banner.style.display = 'none';

    // Switch quality selector to 'high'
    if (qualitySelector) {
        const highOption = qualitySelector.querySelector('[data-quality="high"]');
        const standardOption = qualitySelector.querySelector('[data-quality="standard"]');
        if (highOption && standardOption) {
            standardOption.classList.remove('selected');
            highOption.classList.add('selected');
            highOption.querySelector('input[type="radio"]').checked = true;
        }
    }

    // If file is still selected, auto-submit; otherwise just show the selector
    if (fileInput.files.length > 0) {
        showProgressModal();
        submitFormWithProgress();
    } else {
        showAlert('Please re-select your PDF file, then click Convert.', 'warning');
    }
}

// ===== Feedback Modal =====

function openFeedbackModal(jobId, data) {
    const modal = document.getElementById('feedbackModal');
    if (!modal) return;

    document.getElementById('feedbackJobId').value = jobId || '';
    document.getElementById('feedbackQuality').value = data.quality_used || 'standard';
    document.getElementById('feedbackRows').value = data.extraction_rows || 0;
    document.getElementById('feedbackCols').value = data.extraction_cols || 0;

    // Pre-select feedback type based on confidence
    const typeSelect = document.getElementById('feedbackType');
    const confidence = data.confidence || '';
    if (confidence === 'empty') {
        typeSelect.value = 'empty_result';
    } else if (confidence === 'low') {
        typeSelect.value = 'incorrect_data';
    }

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.focus();
}

function closeFeedbackModal() {
    const modal = document.getElementById('feedbackModal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
}

async function submitFeedback(e) {
    e.preventDefault();
    const form = document.getElementById('feedbackForm');
    const submitBtn = document.getElementById('feedbackSubmitBtn');

    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';

    try {
        const formData = new FormData(form);
        const response = await fetch('/feedback', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: formData
        });

        const result = await response.json();
        closeFeedbackModal();

        if (result.status === 'ok') {
            showAlert('Thank you for your feedback! We\'ll use it to improve.', 'success');
        } else {
            showAlert(result.error || 'Failed to submit feedback.', 'error');
        }
    } catch (err) {
        closeFeedbackModal();
        showAlert('Failed to submit feedback. Please try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit Feedback';
    }
}

// Submit form with progress tracking
async function submitFormWithProgress() {
    try {
        const formData = new FormData(uploadForm);

        const response = await fetch(uploadForm.action, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: formData
        });

        if (response.ok) {
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/json')) {
                const data = await response.json();
                if (data && data.job_id) {
                    startStatusPolling(data.job_id);
                } else {
                    hideProgressModal();
                    showAlert('Unexpected response from server. Please try again.', 'error');
                }
            } else if (contentType && contentType.includes('application/vnd.openxmlformats')) {
                // It's an Excel file - trigger download
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = response.headers.get('content-disposition')?.split('filename=')[1] || 'transactions.xlsx';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                // Show success message
                setTimeout(() => {
                    hideProgressModal();
                    showAlert('✅ Conversion completed successfully! File has been downloaded.', 'success');
                }, 3000);
            } else {
                // Handle HTML response (error page)
                const text = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(text, 'text/html');
                const alerts = doc.querySelectorAll('.alert');

                hideProgressModal();

                if (alerts.length > 0) {
                    alerts.forEach(alert => {
                        const message = alert.textContent.replace('×', '').trim();
                        showAlert(message, alert.className.includes('error') ? 'error' : 'warning');
                    });
                } else {
                    showAlert('Conversion completed, but no file was generated.', 'warning');
                }
            }
        } else {
            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                const data = await response.json();
                if (data.error_code === 'FILE_TOO_LARGE') {
                    hideProgressModal();
                    showLimitModal('FILE_TOO_LARGE', data.max_mb);
                    return;
                }
                if (data.error_code === 'PAGE_LIMIT_EXCEEDED') {
                    hideProgressModal();
                    showLimitModal('PAGE_LIMIT_EXCEEDED', data.max_pages, data.page_count);
                    return;
                }
                if (data.error_code === 'GUEST_LIMIT_EXCEEDED') {
                    hideProgressModal();
                    showAlert('Free limit reached. Please sign in to continue.', 'warning');
                    return;
                }
                throw new Error(data.error || 'Request failed.');
            }
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
    } catch (error) {
        console.error('Form submission error:', error);
        hideProgressModal();
        showAlert(`❌ ${error.message || 'An error occurred during conversion. Please try again.'}`, 'error');
    }
}

// Reset form to initial state
function resetForm() {
    // Reset form fields
    uploadForm.reset();

    // Clear file selection
    clearFileSelection();

    // Reset convert button
    const btnText = convertBtn.querySelector('.btn-text');
    const loadingSpinner = convertBtn.querySelector('.loading-spinner');

    if (btnText && loadingSpinner) {
        btnText.style.display = 'block';
        loadingSpinner.style.display = 'none';
    }

    convertBtn.disabled = true;

    // Check form validity
    checkFormValidity();
}

// Show alert message
function showAlert(message, type = 'error') {
    // Remove existing alerts
    const existingAlerts = document.querySelectorAll('.alert');
    existingAlerts.forEach(alert => alert.remove());

    // Create new alert
    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.setAttribute('role', 'alert');

    const icon = type === 'error' ? 'exclamation-triangle' :
        type === 'warning' ? 'exclamation-circle' :
            type === 'success' ? 'check-circle' : 'info-circle';

    alertDiv.innerHTML = `
        <i class="fas fa-${icon}"></i>
        ${message}
        <button class="close-btn" onclick="this.parentElement.remove()">&times;</button>
    `;

    // Insert alert at top of main content
    const mainContent = document.querySelector('.main-content');
    mainContent.insertBefore(alertDiv, mainContent.firstChild);

    // Auto-remove after appropriate time
    const autoRemoveTime = type === 'success' ? 7000 : 5000;
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, autoRemoveTime);
}

// File upload progress (if needed for larger files)
function showUploadProgress() {
    // This could be implemented with fetch API for real-time progress
    // For now, we use the simple form submission
}

// Utility function to check if browser supports drag and drop
function supportsDragAndDrop() {
    const div = document.createElement('div');
    return ('draggable' in div) || ('ondragstart' in div && 'ondrop' in div);
}

// Initialize drag and drop support check
if (!supportsDragAndDrop()) {
    fileUploadArea.style.borderStyle = 'solid';
    const uploadText = document.querySelector('.upload-text');
    uploadText.innerHTML = '<strong>Click to upload</strong> your PDF file';
}

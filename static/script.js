// SVG icon constants (replacing Font Awesome)
const ICONS = {
    spinner: '<svg class="spinner" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="8" r="6" stroke-opacity="0.25"/><path d="M14 8a6 6 0 00-6-6"/></svg>',
    check: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8.5l3.5 3.5 6.5-7"/></svg>',
    clock: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6"/><path d="M8 4.5v4l2.5 1.5"/></svg>',
    checkCircle: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="8"/><path d="M6.5 10.5l2.5 2.5 5-5"/></svg>',
    warning: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 2L1.5 17h17L10 2zM10 7v4M10 14v.5"/></svg>',
    error: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="8"/><path d="M7 7l6 6M13 7l-6 6"/></svg>',
    info: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="8"/><path d="M10 9v4M10 6.5v.5"/></svg>',
    retry: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 8a6 6 0 0111.5-2.5M14 2v3.5h-3.5"/><path d="M14 8a6 6 0 01-11.5 2.5M2 14v-3.5h3.5"/></svg>',
};

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
    const downloadEmailClose = document.getElementById('downloadEmailClose');
    if (downloadEmailClose) {
        downloadEmailClose.addEventListener('click', closeDownloadEmailModal);
    }
    const downloadEmailForm = document.getElementById('downloadEmailForm');
    if (downloadEmailForm) {
        downloadEmailForm.addEventListener('submit', submitDownloadEmail);
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
    const name = (file.name || '').toLowerCase();
    const isPdfType = file.type === 'application/pdf' ||
        file.type === 'application/x-pdf' ||
        file.type === 'application/octet-stream' ||
        name.endsWith('.pdf');

    if (!isPdfType) {
        showAlert('Please select a PDF file.', 'error');
        return false;
    }

    const maxSizeMb = parseInt(fileInput.dataset.maxSize || '100', 10);
    const maxSize = maxSizeMb * 1024 * 1024;
    if (file.size > maxSize) {
        showLimitModal('FILE_TOO_LARGE', maxSizeMb);
        return false;
    }

    return true;
}

function showLimitModal(type, limitValue, pageCount) {
    if (!limitModal) return;

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
    if (!limitModal) return;
    limitModal.style.display = 'none';
    limitModal.setAttribute('aria-hidden', 'true');
}

// Display file information
function displayFileInfo(file) {
    fileName.textContent = file.name;
    fileSize.textContent = formatFileSize(file.size);

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
        const standardOption = qualitySelector.querySelector('[data-quality="standard"]');
        const highOption = qualitySelector.querySelector('[data-quality="high"]');
        if (standardOption && highOption) {
            standardOption.classList.add('selected');
            highOption.classList.remove('selected');
            standardOption.querySelector('input[type="radio"]').checked = true;
        }
    }
    const retainInputPdf = document.getElementById('retainInputPdf');
    if (retainInputPdf) {
        retainInputPdf.checked = false;
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

// Check form validity
function checkFormValidity() {
    convertBtn.disabled = fileInput.files.length === 0;
}

// Handle form submission
function handleFormSubmit(e) {
    e.preventDefault();
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

function showConversionRequestError(data, fallbackMessage = 'Request failed.') {
    const errorCode = data?.error_code || '';
    if (errorCode === 'FILE_TOO_LARGE') {
        showLimitModal('FILE_TOO_LARGE', data.max_mb);
        return;
    }
    if (errorCode === 'PAGE_LIMIT_EXCEEDED') {
        showLimitModal('PAGE_LIMIT_EXCEEDED', data.max_pages, data.page_count);
        return;
    }
    if (errorCode === 'GUEST_LIMIT_EXCEEDED') {
        showAlert(data.error || 'Free limit reached. Please sign in to continue.', 'warning');
        return;
    }
    if (errorCode === 'USER_LIMIT_EXCEEDED') {
        showAlert(data.error || 'You have reached your conversion limit for this month.', 'warning');
        return;
    }
    if (errorCode === 'RATE_LIMITED') {
        showAlert(data.error || 'Too many conversion requests. Please try again later.', 'warning');
        return;
    }
    if (errorCode === 'INVALID_CSRF') {
        showAlert(data.error || 'Your session expired. Please refresh and try again.', 'error');
        return;
    }
    showAlert(data?.error || fallbackMessage, 'error');
}

async function runConvertPreflight() {
    const csrfInput = uploadForm.querySelector('input[name="csrf_token"]');
    const formData = new FormData();
    if (csrfInput) {
        formData.append('csrf_token', csrfInput.value);
    }

    const response = await fetch('/convert/preflight', {
        method: 'POST',
        headers: {
            'X-Requested-With': 'XMLHttpRequest'
        },
        body: formData
    });

    if (response.ok) {
        return true;
    }

    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
        const data = await response.json();
        hideProgressModal();
        showConversionRequestError(data, `HTTP ${response.status}: ${response.statusText}`);
        return false;
    }

    hideProgressModal();
    showAlert(`HTTP ${response.status}: ${response.statusText}`, 'error');
    return false;
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

        const statusEl = step.querySelector('.step-status');
        if (index === 0) {
            statusEl.innerHTML = ICONS.spinner;
        } else {
            statusEl.innerHTML = ICONS.clock;
        }
    });
}

// Update progress based on real-time data from server
function updateProgress(data) {
    const currentPage = data.current_page ?? data.current ?? 0;
    const totalPages = data.total_pages ?? data.total ?? 0;
    const status = data.status || 'Processing...';
    const percentage = data.percent ?? data.percentage ?? 0;

    progressFill.style.width = percentage + '%';
    if (progressBar) {
        progressBar.setAttribute('aria-valuenow', `${percentage}`);
    }

    if (currentPage > 0 && totalPages > 0) {
        progressInfo.textContent = `${status} - Page ${currentPage} of ${totalPages} (${percentage}%)`;
    } else {
        progressInfo.textContent = status;
    }

    const steps = document.querySelectorAll('.progress-step');

    if (percentage <= 25) {
        updateStepStatus(steps[0], 'active');
        updateStepStatus(steps[1], 'pending');
        updateStepStatus(steps[2], 'pending');
        updateStepStatus(steps[3], 'pending');
    } else if (percentage <= 75) {
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'active');
        updateStepStatus(steps[2], 'pending');
        updateStepStatus(steps[3], 'pending');
    } else if (percentage < 100) {
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'completed');
        updateStepStatus(steps[2], 'active');
        updateStepStatus(steps[3], 'pending');
    } else {
        updateStepStatus(steps[0], 'completed');
        updateStepStatus(steps[1], 'completed');
        updateStepStatus(steps[2], 'completed');
        updateStepStatus(steps[3], 'active');
    }
}

// Update individual step status
function updateStepStatus(step, status) {
    const statusEl = step.querySelector('.step-status');

    step.classList.remove('active', 'completed');
    step.style.opacity = status === 'pending' ? '0.5' : '1';

    if (status === 'active') {
        step.classList.add('active');
        statusEl.innerHTML = ICONS.spinner;
    } else if (status === 'completed') {
        step.classList.add('completed');
        statusEl.innerHTML = ICONS.check;
    } else {
        statusEl.innerHTML = ICONS.clock;
    }
}

let activeJobId = null;
let downloadTriggered = false;
let completionPolls = 0;
let lastJobData = null;
let pendingDownload = null;

function startStatusPolling(jobId) {
    activeJobId = jobId;
    downloadTriggered = false;
    completionPolls = 0;
    lastJobData = null;
    pollJobStatus();
}

function pollJobStatus() {
    if (!activeJobId) return;

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
                hideProgressModal();
                handleDownloadReady(data.download_url, jobId, data);
                return;
            }

            if (status.startsWith('Completed') && !data.download_url) {
                completionPolls += 1;
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

function handleDownloadReady(url, jobId, data) {
    if (data.requires_download_email) {
        openDownloadEmailModal(jobId, data, url);
        return;
    }
    triggerDownload(url);
    showResultBanner(data, jobId);
}

function getFilenameFromDownloadUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        const parts = parsed.pathname.split('/').filter(Boolean);
        return decodeURIComponent(parts[parts.length - 1] || '');
    } catch (err) {
        const parts = String(url || '').split('/').filter(Boolean);
        return decodeURIComponent(parts[parts.length - 1] || '');
    }
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

    banner.className = 'result-banner';
    actions.innerHTML = '';
    meta.textContent = '';

    if (confidence === 'good' && rows > 0) {
        banner.classList.add('success');
        icon.innerHTML = ICONS.checkCircle;
        title.textContent = 'Conversion Complete';
        message.textContent = 'Your file has been downloaded successfully. Was the Excel output accurate?';
        meta.textContent = `${rows} rows \u00d7 ${cols} columns extracted`;

        const successBtn = document.createElement('button');
        successBtn.className = 'btn-feedback';
        successBtn.textContent = 'Looks good';
        successBtn.onclick = () => submitQuickFeedback(jobId, data, 'success', successBtn);
        actions.appendChild(successBtn);

        const fbBtn = document.createElement('button');
        fbBtn.className = 'btn-feedback';
        fbBtn.textContent = 'Report an issue';
        fbBtn.onclick = () => openFeedbackModal(jobId, data);
        actions.appendChild(fbBtn);

    } else if (confidence === 'low') {
        banner.classList.add('warning');
        icon.innerHTML = ICONS.warning;
        title.textContent = 'Partial Extraction';
        message.textContent = qualityMsg || 'Some data may be missing or incomplete.';
        meta.textContent = `${rows} rows extracted \u00b7 ${qualityUsed === 'standard' ? 'Standard' : 'High'} quality`;

        if (qualityUsed === 'standard') {
            const retryBtn = document.createElement('button');
            retryBtn.className = 'btn-retry';
            retryBtn.innerHTML = ICONS.retry + ' Retry in High Quality';
            retryBtn.onclick = () => retryWithHighQuality();
            actions.appendChild(retryBtn);
        }

        const fbBtn = document.createElement('button');
        fbBtn.className = 'btn-feedback';
        fbBtn.textContent = 'Submit feedback';
        fbBtn.onclick = () => openFeedbackModal(jobId, data);
        actions.appendChild(fbBtn);

    } else {
        banner.classList.add('error');
        icon.innerHTML = ICONS.error;

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
            retryBtn.innerHTML = ICONS.retry + ' Retry in High Quality';
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

    if (confidence === 'good') {
        setTimeout(() => { banner.style.display = 'none'; }, 45000);
    }
}

function retryWithHighQuality() {
    const banner = document.getElementById('resultBanner');
    if (banner) banner.style.display = 'none';

    if (qualitySelector) {
        const highOption = qualitySelector.querySelector('[data-quality="high"]');
        const standardOption = qualitySelector.querySelector('[data-quality="standard"]');
        if (highOption && standardOption) {
            standardOption.classList.remove('selected');
            highOption.classList.add('selected');
            highOption.querySelector('input[type="radio"]').checked = true;
        }
    }

    if (fileInput.files.length > 0) {
        showProgressModal();
        submitFormWithProgress();
    } else {
        showAlert('Please re-select your PDF file, then click Convert.', 'warning');
    }
}

// ===== Download Email Modal =====

function openDownloadEmailModal(jobId, data, downloadUrl) {
    const modal = document.getElementById('downloadEmailModal');
    if (!modal) {
        triggerDownload(downloadUrl);
        showResultBanner(data, jobId);
        return;
    }

    pendingDownload = { jobId, data, downloadUrl };
    document.getElementById('downloadEmailJobId').value = jobId || '';
    document.getElementById('downloadEmailFilename').value = getFilenameFromDownloadUrl(downloadUrl);

    modal.style.display = 'flex';
    modal.setAttribute('aria-hidden', 'false');
    modal.focus();
}

function closeDownloadEmailModal() {
    const modal = document.getElementById('downloadEmailModal');
    if (modal) {
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
    }
}

async function submitDownloadEmail(e) {
    e.preventDefault();
    const form = document.getElementById('downloadEmailForm');
    const submitBtn = document.getElementById('downloadEmailSubmitBtn');

    submitBtn.disabled = true;
    submitBtn.textContent = 'Preparing...';

    try {
        const response = await fetch('/download/email', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: new FormData(form)
        });
        const result = await response.json();

        if (result.status !== 'ok' || !result.download_url) {
            throw new Error(result.error || 'Unable to prepare the download.');
        }

        closeDownloadEmailModal();
        triggerDownload(result.download_url);
        if (pendingDownload) {
            showResultBanner(pendingDownload.data, pendingDownload.jobId);
        }
        pendingDownload = null;
    } catch (err) {
        showAlert(err.message || 'Unable to prepare the download. Please try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Download Excel';
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
            showAlert(result.message || 'Thank you for your feedback!', 'success');
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

async function submitQuickFeedback(jobId, data, feedbackType, button) {
    if (!jobId) return;

    const originalText = button ? button.textContent : '';
    if (button) {
        button.disabled = true;
        button.textContent = 'Saving...';
    }

    try {
        const formData = new FormData();
        formData.append('job_id', jobId);
        formData.append('feedback_type', feedbackType);
        formData.append('quality_used', data.quality_used || 'standard');
        formData.append('extraction_rows', data.extraction_rows || 0);
        formData.append('extraction_cols', data.extraction_cols || 0);
        if (feedbackType === 'success') {
            formData.append('message', 'Output marked accurate after download.');
        }

        const response = await fetch('/feedback', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: formData
        });
        const result = await response.json();

        if (result.status === 'ok') {
            showAlert('Thanks for the feedback.', 'success');
            if (button) button.textContent = 'Saved';
        } else {
            showAlert(result.error || 'Failed to submit feedback.', 'error');
            if (button) {
                button.disabled = false;
                button.textContent = originalText;
            }
        }
    } catch (err) {
        showAlert('Failed to submit feedback. Please try again.', 'error');
        if (button) {
            button.disabled = false;
            button.textContent = originalText;
        }
    }
}

// Submit form with progress tracking
async function submitFormWithProgress() {
    try {
        const preflightOk = await runConvertPreflight();
        if (!preflightOk) {
            return;
        }

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
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = response.headers.get('content-disposition')?.split('filename=')[1] || 'transactions.xlsx';
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                setTimeout(() => {
                    hideProgressModal();
                    showAlert('Conversion complete. File has been downloaded.', 'success');
                }, 3000);
            } else {
                const text = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(text, 'text/html');
                const alerts = doc.querySelectorAll('.alert');

                hideProgressModal();

                if (alerts.length > 0) {
                    alerts.forEach(alert => {
                        const message = alert.textContent.replace('\u00d7', '').trim();
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
                hideProgressModal();
                showConversionRequestError(data, 'Request failed.');
                return;
            }
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
    } catch (error) {
        console.error('Form submission error:', error);
        hideProgressModal();
        showAlert(error.message || 'An error occurred during conversion. Please try again.', 'error');
    }
}

// Reset form to initial state
function resetForm() {
    uploadForm.reset();
    clearFileSelection();

    const btnText = convertBtn.querySelector('.btn-text');
    const loadingSpinner = convertBtn.querySelector('.loading-spinner');

    if (btnText && loadingSpinner) {
        btnText.style.display = 'block';
        loadingSpinner.style.display = 'none';
    }

    convertBtn.disabled = true;
    checkFormValidity();
}

// Show alert message
function showAlert(message, type = 'error') {
    const existingAlerts = document.querySelectorAll('.alert');
    existingAlerts.forEach(alert => alert.remove());

    const alertDiv = document.createElement('div');
    alertDiv.className = `alert alert-${type}`;
    alertDiv.setAttribute('role', 'alert');

    let iconSvg;
    if (type === 'error') iconSvg = ICONS.warning;
    else if (type === 'warning') iconSvg = ICONS.info;
    else if (type === 'success') iconSvg = ICONS.checkCircle;
    else iconSvg = ICONS.info;

    alertDiv.innerHTML = `
        ${iconSvg}
        ${message}
        <button class="close-btn" onclick="this.parentElement.remove()">&times;</button>
    `;

    const mainContent = document.querySelector('.main-content');
    mainContent.insertBefore(alertDiv, mainContent.firstChild);

    const autoRemoveTime = type === 'success' ? 7000 : 5000;
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, autoRemoveTime);
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

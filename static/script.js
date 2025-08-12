// DOM Elements
const bankSelect = document.getElementById('bank');
const bankDescription = document.getElementById('bankDescription');
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

// Initialize WebSocket connection
let socket = null;

// Bank descriptions
const bankDescriptions = {
    hdfc: 'Image-based PDF statements (OCR processing)',
    icici: 'Text-based PDF statements'
};

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    checkFormValidity();
    setupEventListeners();
    initializeWebSocket();
});

// Initialize WebSocket connection
function initializeWebSocket() {
    socket = io();
    
    socket.on('connect', function() {
        console.log('Connected to server');
    });
    
    socket.on('progress_update', function(data) {
        updateProgress(data);
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from server');
    });
}

// Setup event listeners
function setupEventListeners() {
    // Bank selection change
    bankSelect.addEventListener('change', function() {
        const selectedBank = this.value;
        if (selectedBank && bankDescriptions[selectedBank]) {
            bankDescription.textContent = bankDescriptions[selectedBank];
            bankDescription.classList.add('show');
        } else {
            bankDescription.classList.remove('show');
        }
        checkFormValidity();
    });

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
    // Check file type
    if (file.type !== 'application/pdf') {
        showAlert('Please select a PDF file.', 'error');
        return false;
    }
    
    // Check file size (100MB limit)
    const maxSize = 100 * 1024 * 1024; // 100MB
    if (file.size > maxSize) {
        showAlert('File size exceeds 100MB limit. Please choose a smaller file.', 'error');
        return false;
    }
    
    return true;
}

// Display file information
function displayFileInfo(file) {
    fileName.textContent = file.name;
    fileSize.textContent = formatFileSize(file.size);
    
    // Hide upload area, show file info
    fileUploadArea.style.display = 'none';
    fileInfo.style.display = 'flex';
}

// Clear file selection
function clearFileSelection() {
    fileInput.value = '';
    fileUploadArea.style.display = 'block';
    fileInfo.style.display = 'none';
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
    const bankSelected = bankSelect.value !== '';
    const fileSelected = fileInput.files.length > 0;
    
    convertBtn.disabled = !(bankSelected && fileSelected);
}

// Handle form submission
function handleFormSubmit(e) {
    e.preventDefault();
    
    // Show progress modal
    showProgressModal();
    
    // Submit form via fetch to handle response properly
    submitFormWithProgress();
}

// Show progress modal
function showProgressModal() {
    progressModal.style.display = 'flex';
    resetProgressSteps();
}

// Hide progress modal and reset form
function hideProgressModal() {
    progressModal.style.display = 'none';
    resetForm();
}

// Reset progress steps
function resetProgressSteps() {
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
    const { current_page, total_pages, status, percentage } = data;
    
    // Update progress bar
    progressFill.style.width = percentage + '%';
    
    // Update progress info text
    if (current_page > 0 && total_pages > 0) {
        progressInfo.textContent = `${status} - Page ${current_page} of ${total_pages} (${percentage}%)`;
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

// Submit form with progress tracking
async function submitFormWithProgress() {
    try {
        const formData = new FormData(uploadForm);
        
        const response = await fetch(uploadForm.action, {
            method: 'POST',
            body: formData
        });
        
        if (response.ok) {
            // Check if response is file download
            const contentType = response.headers.get('content-type');
            if (contentType && contentType.includes('application/vnd.openxmlformats')) {
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
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
    } catch (error) {
        console.error('Form submission error:', error);
        hideProgressModal();
        showAlert('❌ An error occurred during conversion. Please try again.', 'error');
    }
}

// Reset form to initial state
function resetForm() {
    // Reset form fields
    uploadForm.reset();
    bankSelect.value = '';
    bankDescription.classList.remove('show');
    
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
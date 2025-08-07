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

// Bank descriptions
const bankDescriptions = {
    hdfc: 'Image-based PDF statements (OCR processing)',
    icici: 'Text-based PDF statements'
};

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    checkFormValidity();
    setupEventListeners();
});

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
    // Show loading state
    const btnText = convertBtn.querySelector('.btn-text');
    const loadingSpinner = convertBtn.querySelector('.loading-spinner');
    
    btnText.style.display = 'none';
    loadingSpinner.style.display = 'block';
    convertBtn.disabled = true;
    
    // The form will submit normally
    // Loading state will be cleared when page reloads or user returns
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
                 type === 'warning' ? 'exclamation-circle' : 'check-circle';
    
    alertDiv.innerHTML = `
        <i class="fas fa-${icon}"></i>
        ${message}
        <button class="close-btn" onclick="this.parentElement.remove()">&times;</button>
    `;
    
    // Insert alert at top of main content
    const mainContent = document.querySelector('.main-content');
    mainContent.insertBefore(alertDiv, mainContent.firstChild);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        if (alertDiv.parentNode) {
            alertDiv.remove();
        }
    }, 5000);
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
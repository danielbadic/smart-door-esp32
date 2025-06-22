// ==================== UTILITY CLASSES ====================

// Stream management utilities
const StreamManager = {
    pause: function (videoElement) {
        const currentSrc = videoElement.src;
        videoElement.src = '';
        console.log('Stream paused');
        return currentSrc;
    },

    resume: function (videoElement, src, delay = 1000) {
        setTimeout(() => {
            videoElement.src = src;
            console.log('Stream resumed');
        }, delay);
    },

    pauseAndExecute: function (videoElement, actionCallback, resumeDelay = 1000) {
        const currentSrc = this.pause(videoElement);

        return new Promise((resolve) => {
            setTimeout(() => {
                Promise.resolve(actionCallback())
                    .then(resolve)
                    .catch(resolve)
                    .finally(() => {
                        this.resume(videoElement, currentSrc, resumeDelay);
                    });
            }, 500);
        });
    }
};

// Button state management
const ButtonManager = {
    setLoading: function (button, loadingText = 'Se încarcă...') {
        if (!button) return;
        button.disabled = true;
        button.dataset.originalText = button.innerHTML;
        button.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${loadingText}`;
    },

    resetButton: function (button, originalText = null) {
        if (!button) return;
        button.disabled = false;
        button.innerHTML = originalText || button.dataset.originalText || button.innerHTML;
    }
};

// API management
const ApiManager = {
    async request(url, options = {}) {
        try {
            const response = await fetch(url, {
                headers: { 'Content-Type': 'application/json' },
                ...options
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error(`API Error (${url}):`, error);
            throw error;
        }
    },

    async postJson(url, data) {
        return this.request(url, {
            method: 'POST',
            body: JSON.stringify(data)
        });
    },

    async post(url, body = null) {
        return this.request(url, {
            method: 'POST',
            body: body
        });
    },

    async upload(url, formData) {
        const response = await fetch(url, {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            throw new Error(`Upload failed: ${response.status}`);
        }

        return response.json();
    }
};

// UI update manager
const UIUpdateManager = {
    async updateAll() {
        await Promise.all([
            this.updateNotifications(),
            this.updateHistory()
        ]);
    },

    async updateNotifications() {
        try {
            const notifications = await ApiManager.request('/api/notifications');
            updateNotifications(notifications);
        } catch (error) {
            console.error('Error updating notifications:', error);
        }
    },

    async updateHistory() {
        try {
            const [historyData, accessData] = await Promise.all([
                ApiManager.request('/api/history'),
                ApiManager.request('/api/access-history')
            ]);
            updateHistoryView(historyData, accessData);
            updateRecentActivityWithRealData(accessData.slice(0, 5));
            updateStatsWithRealData(accessData);
        } catch (error) {
            console.error('Error updating history:', error);
        }
    }
};

// Date/Time utilities
const DateTimeUtils = {
    formatTimestamp(timestamp) {
        return new Date(timestamp).toLocaleString('ro-RO');
    },

    getTodayString() {
        return new Date().toISOString().split('T')[0];
    },

    isSameDay(date1, date2) {
        const d1 = new Date(date1).toISOString().split('T')[0];
        const d2 = new Date(date2).toISOString().split('T')[0];
        return d1 === d2;
    }
};

// Performance optimization utilities
const PerformanceUtils = {
    debounce(func, wait = 250) {
        let timeout;
        return function executedFunction(...args) {
            clearTimeout(timeout);
            timeout = setTimeout(() => func.apply(this, args), wait);
        };
    },

    throttle(func, limit = 100) {
        let lastCall = 0;
        return function (...args) {
            const now = Date.now();
            if (now - lastCall >= limit) {
                lastCall = now;
                func.apply(this, args);
            }
        };
    }
};

// ==================== GLOBAL VARIABLES ====================

let cameraIP = '';
let streamConnected = false;
let faceDetectionInterval = null;
let isDetecting = false;

// ==================== INITIALIZATION ====================

document.addEventListener('DOMContentLoaded', function () {
    initInterface();
    UIUpdateManager.updateHistory();
    initNotifications();
    initSSE();
    attachEventListeners();
    initVideoStream();
    initFaceManagement();
});

// ==================== VIDEO STREAM MANAGEMENT ====================

function initVideoStream() {
    const savedCameraIP = "192.168.0.100";
    cameraIP = savedCameraIP;

    document.getElementById('camera-ip-setting').value = cameraIP;
    document.getElementById('camera-ip').textContent = cameraIP;

    startVideoStream();
}

function startVideoStream() {
    if (!cameraIP) {
        updateStreamStatus('Configurează IP-ul camerei în setări', false);
        return;
    }

    const videoElement = document.getElementById('video-stream');
    const loadingOverlay = document.getElementById('loading-overlay');
    const streamUrl = `http://${cameraIP}/stream`;

    loadingOverlay.style.display = 'flex';
    videoElement.src = streamUrl;

    videoElement.onload = function () {
        loadingOverlay.style.display = 'none';
        updateStreamStatus('Online', true);
        streamConnected = true;
    };

    videoElement.onerror = function () {
        loadingOverlay.style.display = 'none';
        updateStreamStatus('Conexiune eșuată', false);
        streamConnected = false;
        setTimeout(startVideoStream, 5000);
    };

    setInterval(throttledHealthCheck, 10000);
}

const throttledHealthCheck = PerformanceUtils.throttle(function checkStreamHealth() {
    if (!streamConnected) return;

    const testImg = new Image();
    testImg.onload = () => updateStreamStatus('Online', true);
    testImg.onerror = () => {
        updateStreamStatus('Conexiune pierdută', false);
        streamConnected = false;
        setTimeout(startVideoStream, 2000);
    };
    testImg.src = `http://${cameraIP}/capture?t=${Date.now()}`;
}, 5000);

function updateStreamStatus(message, isOnline) {
    const statusElement = document.getElementById('stream-status');
    const openDoorBtn = document.getElementById('open-door-btn');

    statusElement.innerHTML = `<i class="fas fa-circle"></i> ${message}`;
    statusElement.className = `video-status ${isOnline ? 'status-online' : 'status-offline'}`;

    if (openDoorBtn) {
        openDoorBtn.disabled = !isOnline;
    }
}

// ==================== DOOR CONTROL ====================

async function openDoor() {
    if (!cameraIP || !streamConnected) return;

    const openDoorBtn = document.getElementById('open-door-btn');
    const videoElement = document.getElementById('video-stream');

    ButtonManager.setLoading(openDoorBtn, 'Se deschide...');

    try {
        await StreamManager.pauseAndExecute(videoElement, async () => {
            const data = await ApiManager.post('/api/door/open');
            if (!data.success) {
                showNotification('❌ Eroare: ' + data.message, 'error');
            }
        });
    } catch (error) {
        console.error('Eroare la deschiderea ușii:', error);
        showNotification('❌ Eroare la comunicarea cu serverul', 'error');
    } finally {
        ButtonManager.resetButton(openDoorBtn, '<i class="fas fa-door-open"></i> Deschide Ușa');
    }
}

function refreshStream() {
    const refreshBtn = document.getElementById('refresh-stream-btn');
    const videoElement = document.getElementById('video-stream');

    ButtonManager.setLoading(refreshBtn, 'Se reîncarcă...');
    videoElement.src = '';

    setTimeout(() => {
        startVideoStream();
        ButtonManager.resetButton(refreshBtn, '<i class="fas fa-sync-alt"></i> Reîmprospătează');
    }, 1000);
}

// ==================== INTERFACE MANAGEMENT ====================

function initInterface() {
    const menuItems = document.querySelectorAll('.sidebar nav ul li');
    const sections = document.querySelectorAll('main .content section');

    menuItems.forEach(item => {
        item.addEventListener('click', function () {
            menuItems.forEach(i => i.classList.remove('active'));
            this.classList.add('active');

            const headerTitle = document.querySelector('.header-title h2');
            headerTitle.textContent = this.querySelector('span').textContent;

            const targetId = this.getAttribute('data-target');
            sections.forEach(section => {
                section.classList.remove('active');
                if (section.id === targetId) {
                    section.classList.add('active');
                }
            });
        });
    });

    initNotificationsPanel();
    initImageModal();
}

function initNotificationsPanel() {
    const notificationsToggle = document.getElementById('notifications-toggle');
    const notificationsPanel = document.getElementById('notifications-panel');

    notificationsToggle.addEventListener('click', function () {
        notificationsPanel.classList.toggle('active');
    });

    document.addEventListener('click', function (event) {
        if (!event.target.closest('#notifications-toggle') &&
            !event.target.closest('#notifications-panel') &&
            notificationsPanel.classList.contains('active')) {
            notificationsPanel.classList.remove('active');
        }
    });
}

function initImageModal() {
    const modal = document.getElementById('image-modal');
    const closeModal = document.querySelector('.close-modal');

    closeModal.addEventListener('click', () => modal.classList.remove('active'));
    window.addEventListener('click', (event) => {
        if (event.target === modal) {
            modal.classList.remove('active');
        }
    });
}

// ==================== NOTIFICATIONS ====================

function initNotifications() {
    ApiManager.request('/api/notifications')
        .then(updateNotifications)
        .catch(error => console.error('Eroare:', error));

    document.getElementById('clear-notifications').addEventListener('click', function (e) {
        e.stopPropagation();

        ApiManager.post('/api/notifications/clear')
            .then(data => {
                if (data.success) {
                    document.getElementById('notifications-list').innerHTML = '';
                    document.getElementById('notification-count').textContent = '0';
                }
            })
            .catch(error => console.error('Eroare:', error));
    });
}

function updateNotifications(notifications) {
    const notificationsList = document.getElementById('notifications-list');
    const notificationCount = document.getElementById('notification-count');

    notificationCount.textContent = notifications.length;
    notificationsList.innerHTML = '';

    if (notifications.length === 0) {
        notificationsList.innerHTML = '<div class="notification-item">Nu există notificări.</div>';
        return;
    }

    notifications.forEach(notification => {
        const notificationItem = document.createElement('div');
        notificationItem.className = 'notification-item';

        const formattedTime = DateTimeUtils.formatTimestamp(notification.timestamp);

        notificationItem.innerHTML = `
            <div>Cineva este la ușă!</div>
            <div class="notification-time">${formattedTime}</div>
        `;

        notificationItem.addEventListener('click', function () {
            openImageModal(notification.image_url, notification.filename);
            updateCurrentVisitor(notification);
            document.querySelector('[data-target="dashboard"]').click();
            document.getElementById('notifications-panel').classList.remove('active');
        });

        notificationsList.appendChild(notificationItem);
    });
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `temp-notification ${type}`;
    notification.textContent = message;

    const colors = {
        'success': '#28a745',
        'warning': '#ffc107',
        'error': '#dc3545',
        'info': '#17a2b8'
    };

    notification.style.cssText = `
        position: fixed; top: 20px; right: 20px; padding: 15px 20px;
        border-radius: 5px; color: ${type === 'warning' ? '#212529' : 'white'};
        font-weight: bold; z-index: 10000; max-width: 300px;
        word-wrap: break-word; opacity: 0; transition: opacity 0.3s ease;
        background-color: ${colors[type] || colors.info};
    `;

    document.body.appendChild(notification);
    setTimeout(() => notification.style.opacity = '1', 100);

    setTimeout(() => {
        notification.style.opacity = '0';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

// ==================== HISTORY MANAGEMENT ====================

function updateHistoryView(data, accessData = []) {
    const historyList = document.getElementById('history-list');
    historyList.innerHTML = '';

    if (data.length === 0) {
        historyList.innerHTML = '<p class="empty-state">Nu există înregistrări în istoric.</p>';
        return;
    }

    data.forEach(item => {
        const historyItem = document.createElement('div');
        historyItem.className = 'history-item';

        const accessRecord = accessData.find(record => record.filename === item.filename);
        const { visitorName, statusClass, statusText, methodText } = getVisitorInfo(accessRecord);

        historyItem.innerHTML = `
            <img src="${item.url}" alt="Vizitator" data-filename="${item.filename}">
            <div class="history-info">
                <h4 class="${statusClass === 'status-granted' ? 'known-visitor' : 'unknown-visitor'}">${visitorName}</h4>
                <p>Data: ${item.date}</p>
                <p>Ora: ${item.time}</p>
                ${methodText ? `<p class="method-info">${methodText}</p>` : ''}
                <span class="history-status ${statusClass}">${statusText}</span>
            </div>
        `;

        const img = historyItem.querySelector('img');
        img.addEventListener('click', () => openImageModal(item.url, item.filename));
        historyList.appendChild(historyItem);
    });
}

function updateRecentActivityWithRealData(accessData) {
    const activityList = document.getElementById('recent-activity-list');
    activityList.innerHTML = '';

    if (accessData.length === 0) {
        activityList.innerHTML = '<p class="empty-state">Nu există activitate recentă.</p>';
        return;
    }

    accessData.forEach(item => {
        const activityItem = document.createElement('div');
        activityItem.className = 'activity-item';

        const formattedTime = DateTimeUtils.formatTimestamp(item.timestamp);
        const { visitorName, statusClass, statusText, methodText } = getVisitorInfo(item);

        activityItem.innerHTML = `
            <img src="${item.image_url}" alt="Vizitator" onclick="openImageModal('${item.image_url}', '${item.filename}')">
            <div class="activity-details">
                <p><strong>${visitorName}</strong></p>
                <p>${methodText}</p>
                <p class="activity-time">${formattedTime}</p>
            </div>
            <span class="activity-status ${statusClass}">${statusText}</span>
        `;

        activityList.appendChild(activityItem);
    });
}

function updateStatsWithRealData(accessData) {
    document.getElementById('total-visits').textContent = accessData.length;

    const today = DateTimeUtils.getTodayString();
    const todayVisits = accessData.filter(item =>
        DateTimeUtils.isSameDay(item.timestamp, today)
    ).length;

    document.getElementById('today-visits').textContent = todayVisits;

    const deniedVisits = accessData.filter(item => item.status === 'denied').length;
    document.getElementById('denied-visits').textContent = deniedVisits;
}

function getVisitorInfo(accessRecord) {
    let visitorName = 'Vizitator';
    let statusClass = 'status-unknown';
    let statusText = 'Necunoscut';
    let methodText = '';

    if (accessRecord) {
        if (accessRecord.access_granted && accessRecord.recognized_person) {
            visitorName = accessRecord.recognized_person;
            statusClass = 'status-granted';
            statusText = 'Recunoscut';
        } else if (accessRecord.recognition_result?.includes('recunoscută')) {
            visitorName = 'Persoană recunoscută';
            statusClass = 'status-granted';
            statusText = 'Recunoscut';
        } else {
            visitorName = 'Vizitator necunoscut';
            statusClass = accessRecord.status === 'granted' ? 'status-granted' : 'status-denied';
            statusText = accessRecord.status === 'granted' ? 'Acces permis' : 'Acces refuzat';
        }

        const methodMap = {
            'automatic': '📷 Detectare automată',
            'manual': '📸 Captură manuală',
            'stream_detection': '🎥 Detectare în stream'
        };

        methodText = methodMap[accessRecord.method] ||
            (accessRecord.method?.includes('manual_override') ? '✋ Acces manual' : '👤 Detectare');
    }

    return { visitorName, statusClass, statusText, methodText };
}

// ==================== VISITOR MANAGEMENT ====================

function updateCurrentVisitor(visitorData) {
    const visitorCard = document.getElementById('current-visitor-card');
    const visitorInfo = visitorCard.querySelector('.visitor-info');
    const visitorControls = visitorCard.querySelector('.visitor-controls');

    const formattedTime = DateTimeUtils.formatTimestamp(visitorData.timestamp);
    const { visitorName, statusClass } = getVisitorInfo(visitorData);

    visitorInfo.innerHTML = `
        <img src="${visitorData.image_url}" class="visitor-image" alt="Vizitator">
        <div class="visitor-details">
            <h4 class="${statusClass === 'status-granted' ? 'known-visitor' : 'unknown-visitor'}">${visitorName}</h4>
            <p>Data: ${formattedTime}</p>
            ${visitorData.recognition_result ? `<p class="recognition-status">${visitorData.recognition_result}</p>` : ''}
        </div>
    `;

    if (visitorData.access_granted) {
        visitorControls.classList.add('hidden');
    } else {
        visitorControls.classList.remove('hidden');
    }

    document.getElementById('grant-access').onclick = () => grantAccess(visitorData.filename);
}

function openImageModal(imageUrl, filename) {
    const modal = document.getElementById('image-modal');
    const modalImage = document.getElementById('modal-image');

    modalImage.src = imageUrl;
    modal.classList.add('active');

    document.querySelector('.modal-grant-access').onclick = function () {
        const filename = modalImage.src.split('/').pop();
        grantAccess(filename);
        modal.classList.remove('active');
    };
}

async function grantAccess(filename) {
    const videoElement = document.getElementById('video-stream');

    try {
        await StreamManager.pauseAndExecute(videoElement, async () => {
            const data = await ApiManager.post(`/api/access/grant/${filename}`);

            if (data.success) {
                showNotification('✅ Acces permis cu succes!', 'success');

                const doorData = await ApiManager.post('/api/door/open');
                if (doorData.success) {
                    showNotification('🚪 Ușa a fost deschisă cu succes!', 'success');
                } else {
                    showNotification('❌ Eroare la deschiderea ușii: ' + doorData.message, 'error');
                }
            } else {
                throw new Error(data.message);
            }
        });

        resetCurrentVisitor();
        await UIUpdateManager.updateAll();

    } catch (error) {
        console.error('Eroare:', error);
        showNotification('❌ Eroare: ' + error.message, 'error');
    }
}

function resetCurrentVisitor() {
    const visitorCard = document.getElementById('current-visitor-card');
    visitorCard.querySelector('.visitor-info').innerHTML = '<p>Nu există vizitatori în acest moment.</p>';
    visitorCard.querySelector('.visitor-controls').classList.add('hidden');
}

// ==================== CAPTURE FUNCTIONALITY ====================

async function captureImage(type = 'manual', name = null) {
    const videoElement = document.getElementById('video-stream');
    const button = type === 'manual' ?
        document.getElementById('take-photo') :
        document.getElementById('capture-face');

    ButtonManager.setLoading(button, 'Se capturează...');

    const endpoint = type === 'manual' ? '/take_photo' : '/api/faces/add';
    const payload = type === 'face' ? { name, source: 'capture' } : null;

    try {
        await StreamManager.pauseAndExecute(videoElement, async () => {
            const data = payload ?
                await ApiManager.postJson(endpoint, payload) :
                await ApiManager.post(endpoint);

            if (data.success) {
                showNotification(`✅ ${data.message}`, 'success');

                if (type === 'face') {
                    document.getElementById('capture-form').style.display = 'none';
                    document.getElementById('capture-name').value = '';
                    loadKnownFaces();
                } else {
                    updateCapturePreview(data);
                }

                await UIUpdateManager.updateAll();
            } else {
                showNotification(`❌ Eroare: ${data.message}`, 'error');
            }
        });
    } catch (error) {
        console.error('Capture error:', error);
        showNotification(`❌ Eroare: ${error.message}`, 'error');
    } finally {
        ButtonManager.resetButton(button);
    }
}

function updateCapturePreview(data) {
    const capturePreview = document.getElementById('capture-preview');
    const resultClass = data.access_granted ? 'success' : 'warning';
    const resultIcon = data.access_granted ? 'fa-check-circle' : 'fa-exclamation-triangle';
    const doorMessage = data.access_granted ? ' Ușa a fost deschisă automat!' : '';

    capturePreview.innerHTML = `
        <div class="capture-result ${resultClass}">
            <i class="fas ${resultIcon}"></i>
            <p><strong>${data.message}</strong></p>
            <p>Rezultat: ${data.recognition_result}${doorMessage}</p>
            ${data.access_granted ?
            '<p class="access-granted">✅ ACCES PERMIS</p>' :
            '<p class="access-denied">❌ ACCES REFUZAT</p>'
        }
        </div>
    `;
}

// ==================== FACE DETECTION IN STREAM ====================

const throttledStreamDetection = PerformanceUtils.throttle(function detectFaceInStream() {
    if (isDetecting || !streamConnected || !cameraIP) return;

    isDetecting = true;

    const videoElement = document.getElementById('video-stream');
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');

    canvas.width = videoElement.naturalWidth || 640;
    canvas.height = videoElement.naturalHeight || 480;
    ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(blob => {
        if (blob) {
            const formData = new FormData();
            formData.append('image', blob, 'stream_frame.jpg');

            ApiManager.upload('/api/detect-face-stream', formData)
                .then(data => {
                    // FIX: Check for face_detected instead of access_granted
                    if (data.face_detected) {
                        console.log(`Face detected: ${data.status}, filename: ${data.filename}`);

                        // Pause stream immediately when face is detected
                        pauseStreamForProcessing(videoElement);

                        // STOP detection temporarily while processing
                        stopDetectionTemporarily();
                    }
                })
                .catch(error => console.error('Stream face detection error:', error))
                .finally(() => {
                    isDetecting = false;
                });
        } else {
            isDetecting = false;
        }
    }, 'image/jpeg', 0.8);
}, 2000);

// Pause stream when face is detected and being processed
function pauseStreamForProcessing(videoElement) {
    const currentSrc = videoElement.src;
    const overlay = document.getElementById('face-detection-overlay');
    const detectionText = document.getElementById('detection-text');
    const detectionSubtext = document.getElementById('detection-subtext');

    // Use transparent placeholder
    const transparentPixel = 'data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==';
    videoElement.src = transparentPixel;

    // Overlay
    overlay.classList.add('active');
    console.log('🔄 Stream paused - face detected and being processed');

    // Reset overlay text
    detectionText.textContent = '🔍 DETECTARE FAȚĂ ÎN CURS';
    detectionText.className = 'detection-text';
    detectionSubtext.innerHTML = 'Se analizează imaginea<span class="processing-dots"></span>';

    // Animation
    setTimeout(() => {
        detectionText.textContent = '🤖 PROCESARE RECUNOAȘTERE';
        detectionSubtext.innerHTML = 'Verificare bază de date<span class="processing-dots"></span>';
    }, 1500);

    // Resume stream after processing
    setTimeout(() => {
        videoElement.src = currentSrc;
        overlay.classList.remove('active');
        console.log('▶️ Stream resumed after face processing');

        // Resume detection
        setTimeout(() => {
            restartDetection();
        }, 1000);
    }, 4000);
}

// Stop detection temporarily to avoid spam
function stopDetectionTemporarily() {
    if (faceDetectionInterval) {
        clearInterval(faceDetectionInterval);
        faceDetectionInterval = null;
        console.log('⏸️ Face detection paused temporarily');
    }
}

// Restart detection after processing
function restartDetection() {
    // Only restart if the toggle is still enabled
    const autoDetectionToggle = document.getElementById('auto-detection-toggle');
    if (autoDetectionToggle && autoDetectionToggle.checked && !faceDetectionInterval) {
        faceDetectionInterval = setInterval(throttledStreamDetection, 3000);
        console.log('▶️ Face detection resumed');
    }
}

function startStreamFaceDetection() {
    if (faceDetectionInterval) return;
    faceDetectionInterval = setInterval(throttledStreamDetection, 3000);
    console.log('✅ Face detection started');
}

function stopStreamFaceDetection() {
    if (faceDetectionInterval) {
        clearInterval(faceDetectionInterval);
        faceDetectionInterval = null;
        console.log('❌ Face detection stopped');
    }
}


// ==================== FACE MANAGEMENT ====================

function initFaceManagement() {
    loadKnownFaces();

    document.getElementById('add-face-capture').addEventListener('click', () => {
        document.getElementById('capture-form').style.display = 'block';
        document.getElementById('upload-form').style.display = 'none';
    });

    document.getElementById('add-face-upload').addEventListener('click', () => {
        document.getElementById('upload-form').style.display = 'block';
        document.getElementById('capture-form').style.display = 'none';
    });

    document.getElementById('cancel-upload').addEventListener('click', () => {
        document.getElementById('upload-form').style.display = 'none';
        document.getElementById('face-upload-form').reset();
    });

    document.getElementById('cancel-capture').addEventListener('click', () => {
        document.getElementById('capture-form').style.display = 'none';
        document.getElementById('capture-name').value = '';
    });

    document.getElementById('face-upload-form').addEventListener('submit', function (e) {
        e.preventDefault();
        uploadFace();
    });

    document.getElementById('capture-face').addEventListener('click', () => {
        const name = document.getElementById('capture-name').value.trim();
        if (!name) {
            alert('Vă rugăm să introduceți un nume.');
            return;
        }
        captureImage('face', name);
    });
}

function loadKnownFaces() {
    ApiManager.request('/api/faces')
        .then(updateKnownFacesList)
        .catch(error => console.error('Error loading known faces:', error));
}

function updateKnownFacesList(faces) {
    const facesList = document.getElementById('known-faces-list');
    facesList.innerHTML = '';

    if (faces.length === 0) {
        facesList.innerHTML = '<p class="empty-state">Nu există fețe cunoscute.</p>';
        return;
    }

    faces.forEach(face => {
        const faceItem = document.createElement('div');
        faceItem.className = 'known-face-item';

        faceItem.innerHTML = `
            <img src="${face.url}" alt="${face.name}" class="face-thumbnail">
            <div class="face-info">
                <h5>${face.name}</h5>
                <p>Fișier: ${face.filename}</p>
            </div>
            <div class="face-actions">
                <button class="btn-danger btn-small" onclick="deleteFace('${face.filename}', '${face.name}')">
                    <i class="fas fa-trash"></i>
                </button>
            </div>
        `;

        facesList.appendChild(faceItem);
    });
}

async function uploadFace() {
    const formData = new FormData();
    const fileInput = document.getElementById('face-file');
    const nameInput = document.getElementById('face-name');

    formData.append('file', fileInput.files[0]);
    formData.append('name', nameInput.value);

    try {
        const data = await ApiManager.upload('/api/faces/upload', formData);

        if (data.success) {
            showNotification(data.message, 'success');
            document.getElementById('upload-form').style.display = 'none';
            document.getElementById('face-upload-form').reset();
            loadKnownFaces();
        } else {
            showNotification('Eroare: ' + data.message, 'error');
        }
    } catch (error) {
        console.error('Error uploading face:', error);
        showNotification('Eroare la încărcarea imaginii.', 'error');
    }
}

function deleteFace(filename, name) {
    if (confirm(`Sigur doriți să ștergeți fața pentru ${name}?`)) {
        fetch(`/api/faces/delete/${filename}`, { method: 'DELETE' })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    showNotification(data.message, 'success');
                    loadKnownFaces();
                } else {
                    showNotification('Eroare: ' + data.message, 'error');
                }
            })
            .catch(error => {
                console.error('Error deleting face:', error);
                showNotification('Eroare la ștergerea feței.', 'error');
            });
    }
}

// ==================== SSE EVENTS ====================

const SSEHandlers = {
    'new_visitor': (data) => handleGenericVisitor(data, 'Cineva este la ușă!'),
    'manual_capture_with_recognition': (data) => handleCapture(data),
    'stream_face_recognized': (data) => handleStreamFace(data, true),
    'stream_face_denied': (data) => handleStreamFace(data, false),
    'access_granted_manual': (data) => handleManualGrant(data),
    'telegram_open_door': (data) => handleTelegramOpenDoor(data)
};

function initSSE() {
    const eventSource = new EventSource('/events');

    eventSource.onmessage = function (event) {
        const data = JSON.parse(event.data);
        const handler = SSEHandlers[data.type];

        if (handler) {
            handler(data.data);
        }
    };

    eventSource.onerror = () => console.error('SSE connection error');
}

function handleGenericVisitor(data, title) {
    UIUpdateManager.updateAll();
    playNotificationSound();
    if (document.hidden) {
        showDesktopNotification(title, 'Apăsați pentru a vedea imaginea.');
    }
}

function handleCapture(data) {
    UIUpdateManager.updateAll();
    const message = data.access_granted ?
        '✅ Captură manuală: Persoană recunoscută!' :
        '❌ Captură manuală: Persoană necunoscută';
    showNotification(message, data.access_granted ? 'success' : 'warning');
}

function handleStreamFace(data, isRecognized) {
    UIUpdateManager.updateAll();
    updateCurrentVisitor(data);

    // Update overlay if active
    const overlay = document.getElementById('face-detection-overlay');
    const detectionText = document.getElementById('detection-text');
    const detectionSubtext = document.getElementById('detection-subtext');

    if (overlay.classList.contains('active')) {
        if (isRecognized) {
            detectionText.innerHTML = '✅ ACCES PERMIS';
            detectionText.classList.add('detection-success');
            detectionSubtext.textContent = 'Se deschide ușa...';
        } else {
            detectionText.innerHTML = '❌ ACCES REFUZAT';
            detectionText.classList.add('detection-denied');
            detectionSubtext.textContent = 'Persoană necunoscută';
        }
    }

    const message = isRecognized ?
        '✅ Detectare automată: Persoană recunoscută! Ușa se deschide...' :
        '👤 Detectare automată: Persoană necunoscută la ușă';

    showNotification(message, isRecognized ? 'success' : 'warning');
    playNotificationSound();

    if (document.hidden) {
        const title = isRecognized ? 'Acces permis!' : 'Persoană necunoscută detectată!';
        const body = isRecognized ?
            'Persoană recunoscută detectată automat.' :
            'Verificați cine este la ușă.';
        showDesktopNotification(title, body);
    }
}

function handleManualGrant(data) {
    showNotification('✅ Acces permis manual pentru vizitator', 'success');
    UIUpdateManager.updateAll();
    resetCurrentVisitor();
}

function handleTelegramOpenDoor(data) {
    console.log('[📱] Telegram open door command received via SSE');

    // Show notification that command was received
    showNotification('📱 Telegram: Opening door...', 'info');

    // Use openDoor()
    openDoor();
}

// ==================== SOUND & NOTIFICATIONS ====================

function playNotificationSound() {
    const audioEnabled = document.querySelector('.setting-toggle input')?.checked;
    if (!audioEnabled) return;

    const audio = new Audio('/static/sounds/notification.mp3');
    audio.play().catch(error => console.error('Eroare la redarea sunetului:', error));
}

function showDesktopNotification(title, body) {
    const notificationsEnabled = document.querySelectorAll('.setting-toggle input')[1]?.checked;
    if (!notificationsEnabled) return;

    if (Notification.permission === 'granted') {
        new Notification(title, { body });
    } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(permission => {
            if (permission === 'granted') {
                new Notification(title, { body });
            }
        });
    }
}

// ==================== HISTORY FILTERING ====================

const debouncedFilterHistory = PerformanceUtils.debounce(function filterHistory() {
    const dateFilter = document.getElementById('date-filter').value;
    const statusFilter = document.getElementById('status-filter').value;

    Promise.all([
        ApiManager.request('/api/history'),
        ApiManager.request('/api/access-history')
    ]).then(([historyData, accessData]) => {
        let filteredHistoryData = historyData;

        if (dateFilter) {
            const filterDate = new Date(dateFilter);
            const filterDateStr = filterDate.toLocaleDateString('ro-RO');

            filteredHistoryData = historyData.filter(item => {
                const parts = item.date.split('.');
                if (parts.length === 3) {
                    const itemDate = new Date(parts[2], parts[1] - 1, parts[0]);
                    return itemDate.toLocaleDateString('ro-RO') === filterDateStr;
                }
                return false;
            });
        }

        if (statusFilter !== 'all') {
            filteredHistoryData = filteredHistoryData.filter(item => {
                const accessRecord = accessData.find(record => record.filename === item.filename);
                return accessRecord ? accessRecord.status === statusFilter : statusFilter === 'denied';
            });
        }

        updateHistoryView(filteredHistoryData, accessData);
    }).catch(error => console.error('Error filtering history:', error));
}, 300);

// ==================== EVENT LISTENERS ====================

function attachEventListeners() {
    // Video controls
    document.getElementById('open-door-btn').addEventListener('click', openDoor);
    document.getElementById('refresh-stream-btn').addEventListener('click', refreshStream);


    // Detection togglee
    const autoDetectionToggle = document.getElementById('auto-detection-toggle');
    if (autoDetectionToggle) {
        autoDetectionToggle.addEventListener('change', function () {
            if (this.checked) {
                startStreamFaceDetection();
                console.log('✅ Detectare automată activată');
            } else {
                stopStreamFaceDetection();
                console.log('❌ Detectare automată dezactivată');
            }
        });
    }


    // Settings
    document.getElementById('save-settings').addEventListener('click', function () {
        const newCameraIP = document.getElementById('camera-ip-setting').value.trim();
        if (newCameraIP && newCameraIP !== cameraIP) {
            cameraIP = newCameraIP;
            localStorage.setItem('cameraIP', cameraIP);
            document.getElementById('camera-ip').textContent = cameraIP;
            startVideoStream();
        }
        showNotification('Setările au fost salvate cu succes!', 'success');
    });

    // Access controls
    document.getElementById('deny-access').addEventListener('click', function () {
        resetCurrentVisitor();
        showNotification('Acces refuzat.', 'warning');
    });

    // History filters
    document.getElementById('date-filter').addEventListener('change', debouncedFilterHistory);
    document.getElementById('status-filter').addEventListener('change', debouncedFilterHistory);
}
// ==================== GLOBAL FUNCTIONS ====================

window.openImageModal = openImageModal;
window.grantAccess = grantAccess;
window.deleteFace = deleteFace;
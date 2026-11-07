// Global state
let currentJobId = null;
let currentContentId = null;
const BASE_URL = '/api';

// Tab navigation
function showTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(el => {
        el.classList.remove('active');
    });

    // Remove active from nav
    document.querySelectorAll('.nav-link').forEach(el => {
        el.classList.remove('active');
    });

    // Show selected tab
    const tabId = `tab-${tabName}`;
    const tabEl = document.getElementById(tabId);
    if (tabEl) {
        tabEl.classList.add('active');
    }

    // Mark nav as active
    event.target.closest('.nav-link')?.classList.add('active');

    // Load data if needed
    if (tabName === 'history') {
        loadHistory();
    } else if (tabName === 'dashboard') {
        loadDashboard();
    }
}

// Event listeners
document.getElementById('generateBtn').addEventListener('click', startGeneration);
document.getElementById('approveBtn').addEventListener('click', () => approveContent(true));
document.getElementById('rejectBtn').addEventListener('click', () => approveContent(false));

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    checkHealth();
    loadDashboard();
});

// Generate content
async function startGeneration() {
    const prompt = document.getElementById('prompt').value.trim();
    const location = document.getElementById('location').value;
    const quality = document.getElementById('quality').value;

    if (!prompt) {
        showToast('Por favor, describe el contenido que deseas generar', 'error');
        return;
    }

    // Hide empty state, show processing
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('resultsState').style.display = 'none';
    document.getElementById('detailsPanel').style.display = 'none';
    document.getElementById('processingState').style.display = 'block';

    try {
        // Send request
        const response = await fetch(`${BASE_URL}/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt: prompt,
                comfyui_location: location
            })
        });

        const data = await response.json();
        currentJobId = data.job_id;

        // Start polling
        await pollJobStatus();

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
        resetUI();
    }
}

// Poll job status
async function pollJobStatus() {
    let progress = 10;
    let attempts = 0;
    const maxAttempts = 300;
    const steps = ['copywriter', 'strategist', 'generator', 'editor', 'qa'];

    while (attempts < maxAttempts) {
        try {
            const response = await fetch(`${BASE_URL}/status/${currentJobId}`);
            const job = await response.json();

            // Update progress
            progress = Math.min(progress + Math.random() * 15, 90);
            document.getElementById('progressBar').style.width = progress + '%';

            // Update timeline
            const stepIndex = Math.floor((attempts / maxAttempts) * steps.length);
            steps.forEach((step, i) => {
                const el = document.getElementById(`timeline-${step}`);
                if (!el) return;

                if (i < stepIndex) {
                    el.classList.add('completed');
                    el.classList.remove('active');
                } else if (i === stepIndex) {
                    el.classList.add('active');
                    el.classList.remove('completed');
                } else {
                    el.classList.remove('active', 'completed');
                }
            });

            if (job.status === 'completed') {
                document.getElementById('progressBar').style.width = '100%';
                currentContentId = job.content_id;
                document.getElementById('processingState').style.display = 'none';
                await displayResults(currentContentId);
                break;

            } else if (job.status === 'failed') {
                showToast(`Error: ${job.error}`, 'error');
                resetUI();
                break;
            }

            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;

        } catch (error) {
            console.error('Polling error:', error);
            await new Promise(resolve => setTimeout(resolve, 2000));
            attempts++;
        }
    }

    if (attempts >= maxAttempts) {
        showToast('Timeout en el procesamiento', 'error');
        resetUI();
    }
}

// Display results
async function displayResults(contentId) {
    try {
        const response = await fetch(`${BASE_URL}/content/${contentId}`);
        const content = response.ok ? await response.json() : null;

        if (!content) {
            showToast('No se pudieron cargar los resultados', 'error');
            resetUI();
            return;
        }

        // Display image
        const img = document.getElementById('generatedImage');
        if (content.image_path) {
            img.src = content.image_path;
        }

        // Display prompts
        document.getElementById('originalPrompt').textContent = content.original_prompt;
        document.getElementById('enrichedPrompt').textContent = content.enriched_prompt;

        // Display QA
        const qaDiv = document.getElementById('qaResult');
        qaDiv.innerHTML = `
            <p><strong>Estado:</strong> ${content.qa_passed ? '✅ Pasó' : '⚠️ Revisar'}</p>
            <p><strong>Notas:</strong> ${content.qa_notes || 'Sin notas'}</p>
        `;

        // Display metadata
        const metaDiv = document.getElementById('metadata');
        if (content.metadata) {
            metaDiv.innerHTML = `
                <p><strong>Estilo:</strong> ${content.metadata.style || 'N/A'}</p>
                <p><strong>Plataforma:</strong> ${content.metadata.platform || 'N/A'}</p>
                <p><strong>Aspect ratio:</strong> ${content.metadata.aspect_ratio || 'N/A'}</p>
                <p><strong>Tono:</strong> ${content.metadata.tone || 'N/A'}</p>
            `;
        }

        // Show results
        document.getElementById('resultsState').style.display = 'block';
        document.getElementById('detailsPanel').style.display = 'block';
        document.getElementById('approvalMessage').style.display = 'none';
        document.getElementById('approveBtn').disabled = false;
        document.getElementById('rejectBtn').disabled = false;

        // Load history
        setTimeout(loadHistory, 500);

    } catch (error) {
        showToast(`Error al cargar resultados: ${error.message}`, 'error');
        resetUI();
    }
}

// Approve/Reject
async function approveContent(approved) {
    try {
        const response = await fetch(`${BASE_URL}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content_id: currentContentId,
                approved: approved,
                notes: approved ? 'Aprobado por revisor humano' : 'Rechazado por revisor humano'
            })
        });

        const result = await response.json();
        const msgEl = document.getElementById('approvalMessage');

        msgEl.style.display = 'block';
        msgEl.textContent = result.message;
        msgEl.className = 'approval-msg ' + (approved ? 'success' : 'error');

        document.getElementById('approveBtn').disabled = true;
        document.getElementById('rejectBtn').disabled = true;

        showToast(result.message, approved ? 'success' : 'error');

        setTimeout(loadHistory, 500);

    } catch (error) {
        showToast(`Error: ${error.message}`, 'error');
    }
}

// Load history
async function loadHistory() {
    try {
        const response = await fetch(`${BASE_URL}/history?limit=12`);
        const items = response.ok ? await response.json() : [];

        const historyDiv = document.getElementById('historyGrid');
        const emptyDiv = document.getElementById('emptyHistory');

        if (items.length === 0) {
            historyDiv.style.display = 'none';
            emptyDiv.style.display = 'block';
            return;
        }

        historyDiv.style.display = 'grid';
        emptyDiv.style.display = 'none';
        historyDiv.innerHTML = '';

        items.forEach(item => {
            const badge = item.human_approved ? 'Aprobado' : 'Pendiente';
            const badgeClass = item.human_approved ? 'badge-approved' : 'badge-pending';

            const card = document.createElement('div');
            card.className = 'history-item';
            card.innerHTML = `
                ${item.image_path ? `<img src="${item.image_path}" alt="Contenido" class="history-item-image">` : '<div style="height: 200px; background: rgba(99, 102, 241, 0.1);"></div>'}
                <div class="history-item-content">
                    <div class="history-item-title">${item.original_prompt.substring(0, 50)}...</div>
                    <div class="history-item-date">${new Date(item.created_at).toLocaleDateString('es-ES')}</div>
                    <span class="history-item-badge ${badgeClass}">${badge}</span>
                </div>
            `;
            historyDiv.appendChild(card);
        });

        // Update stats
        updateStats(items);

    } catch (error) {
        console.error('Error loading history:', error);
    }
}

// Load dashboard
async function loadDashboard() {
    try {
        const response = await fetch(`${BASE_URL}/history?limit=100`);
        const items = response.ok ? await response.json() : [];

        const total = items.length;
        const approved = items.filter(i => i.human_approved).length;
        const pending = total - approved;

        document.getElementById('dashTotal').textContent = total;
        document.getElementById('dashApproved').textContent = approved;
        document.getElementById('dashPending').textContent = pending;

        document.getElementById('statGenerated').textContent = total;
        document.getElementById('statApproved').textContent = approved;
        document.getElementById('statProcessing').textContent = pending;

    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

// Update stats
function updateStats(items) {
    const total = items.length;
    const approved = items.filter(i => i.human_approved).length;

    document.getElementById('statGenerated').textContent = total;
    document.getElementById('statApproved').textContent = approved;
    document.getElementById('statProcessing').textContent = total - approved;
}

// Check health
async function checkHealth() {
    try {
        const response = await fetch(`${BASE_URL}/health`);
        const data = await response.json();

        if (response.ok) {
            document.querySelector('.status-indicator').classList.add('online');
            document.getElementById('statusLabel').textContent = 'Sistema activo';
        }
    } catch (error) {
        document.querySelector('.status-indicator').classList.remove('online');
        document.getElementById('statusLabel').textContent = 'Desconectado';
    }
}

// Show toast
function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast toast-${type}`;
    toast.style.display = 'block';

    setTimeout(() => {
        toast.style.display = 'none';
    }, 3000);
}

// Reset UI
function resetUI() {
    document.getElementById('processingState').style.display = 'none';
    document.getElementById('resultsState').style.display = 'none';
    document.getElementById('emptyState').style.display = 'block';
    document.getElementById('prompt').focus();
}

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') {
        startGeneration();
    }
});

/**
 * Trading System JavaScript for Farmington
 * Handles trade proposal validation, UI interactions, and real-time updates
 */

class TradingManager {
    constructor() {
        this.init();
    }

    /**
     * Initialize trading system
     */
    init() {
        this.setupEventListeners();
        this.setupFormValidation();
        this.setupAutoRefresh();
        this.initializeTooltips();
    }

    /**
     * Setup event listeners for trading interactions
     */
    setupEventListeners() {
        // Trade form submission
        const tradeForm = document.getElementById('trade-form');
        if (tradeForm) {
            tradeForm.addEventListener('submit', this.handleTradeSubmission.bind(this));
        }

        // Trade response buttons
        this.setupTradeResponseHandlers();

        // Farmer selection change handlers
        this.setupFarmerSelectionHandlers();

        // Trade preview
        this.setupTradePreview();
    }

    /**
     * Handle trade form submission with validation
     */
    handleTradeSubmission(event) {
        const form = event.target;
        const formData = new FormData(form);
        
        const targetUser = formData.get('target_user');
        const offeredRole = formData.get('offered_role');
        const requestedRole = formData.get('requested_role');

        // Validate all fields are filled
        if (!targetUser || !offeredRole || !requestedRole) {
            event.preventDefault();
            this.showAlert('Please fill in all required fields.', 'warning');
            return false;
        }

        // Validate not trading with self (shouldn't happen, but safety check)
        const currentUser = this.getCurrentUsername();
        if (targetUser === currentUser) {
            event.preventDefault();
            this.showAlert('You cannot trade with yourself!', 'danger');
            return false;
        }

        // Show confirmation dialog
        const confirmation = this.showTradeConfirmation(formData);
        if (!confirmation) {
            event.preventDefault();
            return false;
        }

        // Add loading state
        this.setFormLoading(form, true);
        
        return true;
    }

    /**
     * Setup trade response handlers for accept/reject buttons
     */
    setupTradeResponseHandlers() {
        const acceptButtons = document.querySelectorAll('button[name="action"][value="accept"]');
        const rejectButtons = document.querySelectorAll('button[name="action"][value="reject"]');

        acceptButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const tradeData = this.extractTradeData(button);
                if (!this.confirmTradeAcceptance(tradeData)) {
                    e.preventDefault();
                }
            });
        });

        rejectButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                if (!confirm('Are you sure you want to reject this trade offer?')) {
                    e.preventDefault();
                }
            });
        });
    }

    /**
     * Setup farmer selection change handlers
     */
    setupFarmerSelectionHandlers() {
        const targetUserSelect = document.querySelector('select[name="target_user"]');
        const offeredRoleSelect = document.querySelector('select[name="offered_role"]');
        const requestedRoleSelect = document.querySelector('select[name="requested_role"]');

        if (targetUserSelect) {
            targetUserSelect.addEventListener('change', this.updateRequestedRoleOptions.bind(this));
        }

        if (offeredRoleSelect) {
            offeredRoleSelect.addEventListener('change', this.updateTradePreview.bind(this));
        }

        if (requestedRoleSelect) {
            requestedRoleSelect.addEventListener('change', this.updateTradePreview.bind(this));
        }
    }

    /**
     * Setup trade preview functionality
     */
    setupTradePreview() {
        const previewContainer = document.getElementById('trade-preview');
        if (!previewContainer) {
            // Create preview container if it doesn't exist
            this.createTradePreviewContainer();
        }
    }

    /**
     * Create trade preview container
     */
    createTradePreviewContainer() {
        const tradeForm = document.getElementById('trade-form');
        if (!tradeForm) return;

        const previewContainer = document.createElement('div');
        previewContainer.id = 'trade-preview';
        previewContainer.className = 'mt-4 p-3 border rounded bg-light d-none';
        previewContainer.innerHTML = `
            <h6><i class="fas fa-eye me-2"></i>Trade Preview</h6>
            <div class="row">
                <div class="col-md-5">
                    <div class="trade-preview-card bg-danger text-white p-2 rounded">
                        <div id="preview-offered">Select farmer to offer</div>
                    </div>
                </div>
                <div class="col-md-2 text-center">
                    <i class="fas fa-exchange-alt fa-2x text-warning mt-2"></i>
                </div>
                <div class="col-md-5">
                    <div class="trade-preview-card bg-success text-white p-2 rounded">
                        <div id="preview-requested">Select role to request</div>
                    </div>
                </div>
            </div>
        `;

        tradeForm.parentNode.insertBefore(previewContainer, tradeForm.nextSibling);
    }

    /**
     * Update trade preview when selections change
     */
    updateTradePreview() {
        const previewContainer = document.getElementById('trade-preview');
        if (!previewContainer) return;

        const offeredRole = document.querySelector('select[name="offered_role"]').value;
        const requestedRole = document.querySelector('select[name="requested_role"]').value;
        const targetUser = document.querySelector('select[name="target_user"]').value;

        const previewOffered = document.getElementById('preview-offered');
        const previewRequested = document.getElementById('preview-requested');

        if (offeredRole) {
            const offeredText = document.querySelector(`select[name="offered_role"] option[value="${offeredRole}"]`).textContent;
            previewOffered.innerHTML = `<strong>You Give:</strong><br>${offeredText}`;
        } else {
            previewOffered.textContent = 'Select farmer to offer';
        }

        if (requestedRole && targetUser) {
            previewRequested.innerHTML = `<strong>You Get:</strong><br>${targetUser}'s ${requestedRole}`;
        } else {
            previewRequested.textContent = 'Select role to request';
        }

        // Show/hide preview
        if (offeredRole || requestedRole) {
            previewContainer.classList.remove('d-none');
        } else {
            previewContainer.classList.add('d-none');
        }
    }

    /**
     * Update requested role options based on selected user
     */
    updateRequestedRoleOptions() {
        // This would require AJAX to get the target user's team
        // For now, just show all available roles
        const requestedRoleSelect = document.querySelector('select[name="requested_role"]');
        if (requestedRoleSelect) {
            // Enable all options
            Array.from(requestedRoleSelect.options).forEach(option => {
                option.disabled = false;
            });
        }
    }

    /**
     * Setup form validation
     */
    setupFormValidation() {
        const forms = document.querySelectorAll('form[method="post"]');
        forms.forEach(form => {
            form.addEventListener('submit', (e) => {
                if (!this.validateForm(form)) {
                    e.preventDefault();
                }
            });
        });
    }

    /**
     * Validate form inputs
     */
    validateForm(form) {
        const requiredFields = form.querySelectorAll('[required]');
        let isValid = true;

        requiredFields.forEach(field => {
            if (!field.value.trim()) {
                this.highlightInvalidField(field);
                isValid = false;
            } else {
                this.clearFieldHighlight(field);
            }
        });

        return isValid;
    }

    /**
     * Highlight invalid form field
     */
    highlightInvalidField(field) {
        field.classList.add('is-invalid');
        field.addEventListener('input', () => {
            if (field.value.trim()) {
                this.clearFieldHighlight(field);
            }
        }, { once: true });
    }

    /**
     * Clear field highlight
     */
    clearFieldHighlight(field) {
        field.classList.remove('is-invalid');
    }

    /**
     * Show trade confirmation dialog
     */
    showTradeConfirmation(formData) {
        const targetUser = formData.get('target_user');
        const offeredRole = formData.get('offered_role');
        const requestedRole = formData.get('requested_role');
        const message = formData.get('message');

        let confirmText = `Send trade proposal to ${targetUser}?\n\n`;
        confirmText += `You will offer: ${offeredRole}\n`;
        confirmText += `You will request: ${requestedRole}`;
        
        if (message) {
            confirmText += `\n\nMessage: "${message}"`;
        }

        return confirm(confirmText);
    }

    /**
     * Confirm trade acceptance
     */
    confirmTradeAcceptance(tradeData) {
        let confirmText = `Accept this trade?\n\n`;
        confirmText += `You will give up: ${tradeData.yourFarmer}\n`;
        confirmText += `You will receive: ${tradeData.theirFarmer}\n\n`;
        confirmText += `This action cannot be undone.`;

        return confirm(confirmText);
    }

    /**
     * Extract trade data from button context
     */
    extractTradeData(button) {
        const tradeCard = button.closest('.card');
        const yourFarmerEl = tradeCard.querySelector('.bg-danger .card-title, .bg-danger strong');
        const theirFarmerEl = tradeCard.querySelector('.bg-success .card-title, .bg-success strong');

        return {
            yourFarmer: yourFarmerEl ? yourFarmerEl.textContent : 'Unknown',
            theirFarmer: theirFarmerEl ? theirFarmerEl.textContent : 'Unknown'
        };
    }

    /**
     * Set form loading state
     */
    setFormLoading(form, loading) {
        const submitButton = form.querySelector('button[type="submit"]');
        if (!submitButton) return;

        if (loading) {
            submitButton.disabled = true;
            submitButton.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Sending...';
        } else {
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="fas fa-paper-plane me-2"></i>Send Trade Proposal';
        }
    }

    /**
     * Show alert message
     */
    showAlert(message, type = 'info') {
        const alertContainer = this.getOrCreateAlertContainer();
        
        const alert = document.createElement('div');
        alert.className = `alert alert-${type} alert-dismissible fade show`;
        alert.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;

        alertContainer.appendChild(alert);

        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            if (alert.parentNode) {
                alert.remove();
            }
        }, 5000);
    }

    /**
     * Get or create alert container
     */
    getOrCreateAlertContainer() {
        let container = document.getElementById('trading-alerts');
        if (!container) {
            container = document.createElement('div');
            container.id = 'trading-alerts';
            container.className = 'position-fixed top-0 end-0 p-3';
            container.style.zIndex = '1050';
            document.body.appendChild(container);
        }
        return container;
    }

    /**
     * Setup auto-refresh for trade updates
     */
    setupAutoRefresh() {
        // Refresh every 30 seconds to check for new trades
        setInterval(() => {
            this.checkForTradeUpdates();
        }, 30000);
    }

    /**
     * Check for trade updates
     */
    async checkForTradeUpdates() {
        try {
            const response = await fetch('/trading', {
                method: 'GET',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest'
                }
            });

            if (response.ok) {
                // Could implement partial page updates here
                // For now, just refresh if there are new trades
                const text = await response.text();
                if (this.hasNewTrades(text)) {
                    this.showAlert('New trade activity detected. Refreshing page...', 'info');
                    setTimeout(() => location.reload(), 2000);
                }
            }
        } catch (error) {
            console.warn('Could not check for trade updates:', error);
        }
    }

    /**
     * Check if there are new trades compared to current page
     */
    hasNewTrades(responseText) {
        // Simple check - compare number of trade cards
        const currentTradeCount = document.querySelectorAll('.card.border-primary, .card.border-info').length;
        const parser = new DOMParser();
        const doc = parser.parseFromString(responseText, 'text/html');
        const newTradeCount = doc.querySelectorAll('.card.border-primary, .card.border-info').length;
        
        return newTradeCount !== currentTradeCount;
    }

    /**
     * Initialize tooltips for trade cards
     */
    initializeTooltips() {
        // Add tooltips to farmer stat displays
        const statElements = document.querySelectorAll('.farmer-trade-card small');
        statElements.forEach(el => {
            el.title = 'Farmer Statistics: Strength, Handy, Stamina, Physical';
        });

        // Add tooltips to trade status badges
        const statusBadges = document.querySelectorAll('.badge');
        statusBadges.forEach(badge => {
            if (badge.textContent.includes('Pending')) {
                badge.title = 'Waiting for response from other player';
            } else if (badge.textContent.includes('Accepted')) {
                badge.title = 'Trade completed successfully';
            } else if (badge.textContent.includes('Rejected')) {
                badge.title = 'Trade was declined';
            }
        });
    }

    /**
     * Get current username from page context
     */
    getCurrentUsername() {
        const userElement = document.querySelector('[data-username]');
        return userElement ? userElement.dataset.username : null;
    }

    /**
     * Animate trade card interactions
     */
    animateTradeCard(card, type = 'accept') {
        card.style.transition = 'all 0.3s ease';
        
        if (type === 'accept') {
            card.style.borderColor = '#28a745';
            card.style.backgroundColor = '#d4edda';
        } else if (type === 'reject') {
            card.style.borderColor = '#dc3545';
            card.style.backgroundColor = '#f8d7da';
        }

        setTimeout(() => {
            card.style.transition = '';
            card.style.borderColor = '';
            card.style.backgroundColor = '';
        }, 1000);
    }
}

// Initialize trading manager when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new TradingManager();
});

// Add trade statistics tracking
class TradeStats {
    static trackTradeProposal(fromUser, toUser, offeredRole, requestedRole) {
        // Could send analytics data here
        console.log(`Trade proposed: ${fromUser} offers ${offeredRole} for ${toUser}'s ${requestedRole}`);
    }

    static trackTradeResponse(tradeId, response) {
        console.log(`Trade ${tradeId} ${response}`);
    }
}

// Export for potential module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TradingManager, TradeStats };
}

/**
 * Theme Management System for Farmington
 * Handles dark/light mode toggle with smooth transitions and persistent storage
 */

class ThemeManager {
    constructor() {
        this.currentTheme = 'light';
        this.transitionDuration = 300;
        this.init();
    }

    /**
     * Initialize theme manager
     */
    init() {
        this.loadTheme();
        this.setupEventListeners();
        this.applyTransitionClasses();
    }

    /**
     * Load theme from server or localStorage
     */
    async loadTheme() {
        try {
            // Try to get theme from server if user is logged in
            const response = await fetch('/get_theme');
            if (response.ok) {
                const data = await response.json();
                this.currentTheme = data.theme || 'light';
            } else {
                // Fallback to localStorage
                this.currentTheme = localStorage.getItem('farmington-theme') || 'light';
            }
        } catch (error) {
            console.warn('Could not load theme from server, using localStorage');
            this.currentTheme = localStorage.getItem('farmington-theme') || 'light';
        }

        this.applyTheme(this.currentTheme, false);
    }

    /**
     * Apply theme to document
     */
    applyTheme(theme, animate = true) {
        const html = document.documentElement;
        const body = document.body;

        if (animate) {
            // Add transition classes for smooth animation
            body.style.transition = `background-color ${this.transitionDuration}ms ease-in-out, color ${this.transitionDuration}ms ease-in-out`;
            
            // Apply transition to all elements that might change
            const elements = document.querySelectorAll('.card, .navbar, .btn, .form-control, .form-select, .table, .alert');
            elements.forEach(el => {
                el.style.transition = `all ${this.transitionDuration}ms ease-in-out`;
            });
        }

        // Set theme attribute
        html.setAttribute('data-theme', theme);
        
        // Update current theme
        this.currentTheme = theme;

        // Save to localStorage
        localStorage.setItem('farmington-theme', theme);

        // Update Bootstrap theme classes if needed
        this.updateBootstrapClasses(theme);

        // Update theme toggle icon
        this.updateThemeToggleIcon(theme);

        if (animate) {
            // Remove transition styles after animation completes
            setTimeout(() => {
                body.style.transition = '';
                const elements = document.querySelectorAll('.card, .navbar, .btn, .form-control, .form-select, .table, .alert');
                elements.forEach(el => {
                    el.style.transition = '';
                });
            }, this.transitionDuration);
        }
    }

    /**
     * Update Bootstrap theme-specific classes
     */
    updateBootstrapClasses(theme) {
        const elements = document.querySelectorAll('[class*="table-"]');
        elements.forEach(el => {
            if (theme === 'dark') {
                if (el.classList.contains('table-light')) {
                    el.classList.remove('table-light');
                    el.classList.add('table-dark');
                }
            } else {
                if (el.classList.contains('table-dark')) {
                    el.classList.remove('table-dark');
                    el.classList.add('table-light');
                }
            }
        });
    }

    /**
     * Update theme toggle icon
     */
    updateThemeToggleIcon(theme) {
        const toggleIcons = document.querySelectorAll('.theme-toggle-icon');
        toggleIcons.forEach(icon => {
            if (theme === 'dark') {
                icon.classList.remove('fa-moon');
                icon.classList.add('fa-sun');
            } else {
                icon.classList.remove('fa-sun');
                icon.classList.add('fa-moon');
            }
        });
    }

    /**
     * Toggle between light and dark themes
     */
    async toggleTheme() {
        const newTheme = this.currentTheme === 'light' ? 'dark' : 'light';
        
        // Apply theme immediately for better UX
        this.applyTheme(newTheme, true);

        // Save to server if user is logged in
        try {
            const response = await fetch('/set_theme', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ theme: newTheme })
            });
            
            if (!response.ok) {
                console.warn('Could not save theme to server');
            }
        } catch (error) {
            console.warn('Could not save theme to server:', error);
        }
    }

    /**
     * Setup event listeners
     */
    setupEventListeners() {
        // Listen for storage changes from other tabs
        window.addEventListener('storage', (e) => {
            if (e.key === 'farmington-theme') {
                this.applyTheme(e.newValue, true);
            }
        });

        // Listen for system theme changes
        if (window.matchMedia) {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            mediaQuery.addEventListener('change', (e) => {
                // Only auto-switch if user hasn't set a preference
                if (!localStorage.getItem('farmington-theme')) {
                    this.applyTheme(e.matches ? 'dark' : 'light', true);
                }
            });
        }
    }

    /**
     * Apply transition classes for smooth animations
     */
    applyTransitionClasses() {
        document.addEventListener('DOMContentLoaded', () => {
            // Add theme transition class to body
            document.body.classList.add('theme-transition');
            
            // Add transition classes to elements that change with theme
            const elements = document.querySelectorAll('.card, .navbar, .table, .form-control, .form-select');
            elements.forEach(el => {
                el.classList.add('theme-transition');
            });
        });
    }

    /**
     * Get current theme
     */
    getCurrentTheme() {
        return this.currentTheme;
    }

    /**
     * Set theme programmatically
     */
    setTheme(theme) {
        if (theme === 'light' || theme === 'dark') {
            this.applyTheme(theme, true);
        }
    }

    /**
     * Reset to system preference
     */
    resetToSystemPreference() {
        localStorage.removeItem('farmington-theme');
        
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            this.applyTheme('dark', true);
        } else {
            this.applyTheme('light', true);
        }
    }
}

// Initialize theme manager
const themeManager = new ThemeManager();

// Global function for theme toggle (called from template)
function toggleTheme() {
    themeManager.toggleTheme();
}

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ThemeManager;
}

// Add theme preferences detection
document.addEventListener('DOMContentLoaded', () => {
    // Detect system preference if no saved preference exists
    if (!localStorage.getItem('farmington-theme')) {
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            themeManager.setTheme('dark');
        }
    }

    // Add keyboard shortcut for theme toggle (Ctrl/Cmd + Shift + T)
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
            e.preventDefault();
            toggleTheme();
        }
    });

    // Add visual feedback for theme changes
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'data-theme') {
                // Add subtle flash effect when theme changes
                document.body.style.opacity = '0.95';
                setTimeout(() => {
                    document.body.style.opacity = '';
                }, 100);
            }
        });
    });

    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme']
    });
});

// Theme persistence across navigation
window.addEventListener('beforeunload', () => {
    localStorage.setItem('farmington-theme', themeManager.getCurrentTheme());
});

// Auto-apply theme on page load for better performance
(function() {
    const savedTheme = localStorage.getItem('farmington-theme');
    if (savedTheme) {
        document.documentElement.setAttribute('data-theme', savedTheme);
    }
})();

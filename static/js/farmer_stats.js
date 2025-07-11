
/**
 * Farmer Stats Table Sorting Functionality
 */

class FarmerStatsTable {
    constructor() {
        this.table = document.getElementById('farmer-stats-table');
        this.tbody = this.table ? this.table.querySelector('tbody') : null;
        this.currentSort = { column: null, direction: 'asc' };
        
        if (this.table && this.tbody) {
            this.init();
        }
    }

    init() {
        this.setupSortingEventListeners();
    }

    setupSortingEventListeners() {
        const sortableHeaders = this.table.querySelectorAll('th.sortable');
        
        sortableHeaders.forEach(header => {
            header.addEventListener('click', () => {
                const column = header.dataset.column;
                const type = header.dataset.type || 'string';
                this.sortTable(column, type, header);
            });
            
            // Add cursor pointer style
            header.style.cursor = 'pointer';
        });
    }

    sortTable(column, type, headerElement) {
        const rows = Array.from(this.tbody.querySelectorAll('tr'));
        
        // Determine sort direction
        let direction = 'asc';
        if (this.currentSort.column === column && this.currentSort.direction === 'asc') {
            direction = 'desc';
        }
        
        // Update current sort state
        this.currentSort = { column, direction };
        
        // Update header icons
        this.updateSortIcons(headerElement, direction);
        
        // Sort the rows
        rows.sort((a, b) => {
            let aValue = this.getCellValue(a, column, type);
            let bValue = this.getCellValue(b, column, type);
            
            return this.compareValues(aValue, bValue, type, direction);
        });
        
        // Re-append sorted rows to tbody
        rows.forEach(row => this.tbody.appendChild(row));
    }

    getCellValue(row, column, type) {
        // First try to get value from data attribute
        let value = row.dataset[column];
        
        if (value !== undefined && value !== null) {
            // Handle special cases for data attributes
            if (type === 'number') {
                // Handle special case for vs_your_role_diff where 999999 means null
                if (column === 'vs_your_role_diff' && value === '999999') {
                    return null;
                }
                return parseFloat(value) || 0;
            }
            return value;
        }
        
        // Fallback to cell text content if no data attribute
        const columnIndex = this.getColumnIndex(column);
        if (columnIndex >= 0) {
            const cell = row.cells[columnIndex];
            const cellText = cell ? cell.textContent.trim() : '';
            
            if (type === 'number') {
                // Extract numeric value from text
                const numMatch = cellText.match(/-?\d+(\.\d+)?/);
                return numMatch ? parseFloat(numMatch[0]) : 0;
            }
            
            return cellText;
        }
        
        return '';
    }

    getColumnIndex(column) {
        const headers = this.table.querySelectorAll('th');
        for (let i = 0; i < headers.length; i++) {
            if (headers[i].dataset.column === column) {
                return i;
            }
        }
        return -1;
    }

    compareValues(a, b, type, direction) {
        const multiplier = direction === 'asc' ? 1 : -1;
        
        // Handle null values (put them at the end)
        if (a === null && b === null) return 0;
        if (a === null) return multiplier;
        if (b === null) return -multiplier;
        
        if (type === 'number') {
            return (a - b) * multiplier;
        } else {
            // String comparison (case insensitive)
            const aStr = String(a).toLowerCase();
            const bStr = String(b).toLowerCase();
            
            if (aStr < bStr) return -1 * multiplier;
            if (aStr > bStr) return 1 * multiplier;
            return 0;
        }
    }

    updateSortIcons(activeHeader, direction) {
        // Reset all sort icons
        const allHeaders = this.table.querySelectorAll('th.sortable i');
        allHeaders.forEach(icon => {
            icon.className = 'fas fa-sort text-muted';
        });
        
        // Update active header icon
        const activeIcon = activeHeader.querySelector('i');
        if (activeIcon) {
            if (direction === 'asc') {
                activeIcon.className = 'fas fa-sort-up text-primary';
            } else {
                activeIcon.className = 'fas fa-sort-down text-primary';
            }
        }
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    new FarmerStatsTable();
});

// Re-initialize if table is dynamically updated
window.initFarmerStatsTable = () => {
    new FarmerStatsTable();
};

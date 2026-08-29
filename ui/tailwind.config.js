/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        canvas: '#F6F5F9',
        surface: '#FFFFFF',
        'surface-white': '#FFFFFF',
        'surface-subtle': '#EBE8F6',
        'surface-elevated': '#F8F7FD',
        'dark-base': '#14121F',
        'brand-primary': '#2E2682',
        'brand-purple': '#6B46C1',
        'brand-purple-dark': '#4C1D95',
        'accent-orange': '#F2740E',
        'risk-high': '#E23636',
        'risk-elevated': '#F2740E',
        'risk-routine': '#645F78',
        'status-cleared': '#10B981',
        'border-subtle': '#E2DFED',
        'border-focus': '#2E2682',
        primary: '#14121F',
        secondary: '#4A4560',
        muted: '#7E7998',
        'text-primary': '#14121F',
        'text-secondary': '#4A4560',
        'text-muted': '#7E7998',
      },
      fontFamily: {
        sans: ['Onest', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['Space Mono', 'JetBrains Mono', 'SF Mono', 'ui-monospace', 'monospace'],
      },
      boxShadow: {
        'aegis-subtle': '0 1px 3px rgba(20, 18, 31, 0.04), 0 1px 2px rgba(20, 18, 31, 0.02)',
        'aegis-card': '0 4px 16px -2px rgba(20, 18, 31, 0.06), 0 2px 6px -1px rgba(20, 18, 31, 0.03)',
        'aegis-popover': '0 12px 32px -4px rgba(20, 18, 31, 0.12), 0 4px 12px -2px rgba(20, 18, 31, 0.04)',
        'aegis-glow': '0 0 24px rgba(107, 70, 193, 0.25)',
      },
    },
  },
  plugins: [],
}


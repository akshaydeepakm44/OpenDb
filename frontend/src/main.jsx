import React, { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an unhandled render exception:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          background: '#080e1a',
          color: '#f8fafc',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '2rem',
          fontFamily: 'Inter, -apple-system, sans-serif'
        }}>
          <div style={{
            background: '#0f172a',
            border: '1px solid #ef4444',
            borderRadius: '1rem',
            padding: '2rem',
            maxWidth: '620px',
            width: '100%',
            textAlign: 'center',
            boxShadow: '0 25px 50px -12px rgba(239, 68, 68, 0.25)'
          }}>
            <div style={{ fontSize: '3rem', marginBottom: '1rem' }}>🛡️</div>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 900, color: '#f87171', margin: '0 0 0.75rem 0' }}>
              View Component Interruption Caught
            </h2>
            <p style={{ fontSize: '0.88rem', color: '#94a3b8', lineHeight: '1.5', margin: '0 0 1.25rem 0' }}>
              An unexpected payload structure was encountered. The interface was prevented from blanking out.
            </p>
            <div style={{
              background: '#040812',
              border: '1px solid #334155',
              borderRadius: '0.5rem',
              padding: '0.75rem 1rem',
              fontSize: '0.78rem',
              color: '#fca5a5',
              fontFamily: 'monospace',
              textAlign: 'left',
              marginBottom: '1.5rem',
              maxHeight: '120px',
              overflowY: 'auto'
            }}>
              {this.state.error?.toString() || 'Unknown error'}
            </div>
            <button
              onClick={() => {
                this.setState({ hasError: false, error: null });
                window.location.reload();
              }}
              style={{
                background: '#38bdf8',
                color: '#000',
                fontWeight: 800,
                fontSize: '0.85rem',
                padding: '0.65rem 1.5rem',
                border: 'none',
                borderRadius: '0.5rem',
                cursor: 'pointer'
              }}
            >
              🔄 Reload Application
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)


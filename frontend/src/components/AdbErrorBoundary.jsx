import { Component } from 'react';

export class AdbErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ADB control error:', error, errorInfo);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 16, border: '1px solid var(--red)', borderRadius: 6, background: 'var(--surface2)' }}>
          <div style={{ color: 'var(--red)', fontWeight: 600, marginBottom: 8 }}>
            ADB Control Error
          </div>
          <div style={{ color: 'var(--muted)', fontSize: '0.85rem', marginBottom: 12 }}>
            {this.state.error.message}
          </div>
          <button
            className="btn"
            onClick={() => this.setState({ error: null })}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

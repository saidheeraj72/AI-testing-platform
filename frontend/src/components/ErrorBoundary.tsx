import { Component, type ReactNode } from "react";

/** Keeps a rendering error in one page from blanking the whole app. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return (
        <div className="error-box" role="alert">
          This page failed to display: {this.state.error.message}. The test itself is not affected; reload the page to
          try again.
        </div>
      );
    }
    return this.props.children;
  }
}

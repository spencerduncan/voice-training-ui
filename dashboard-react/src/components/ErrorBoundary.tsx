import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  // what to show instead; a function gets the error (for the top-level card)
  fallback: ReactNode | ((error: Error) => ReactNode);
  // called once per caught error — use it to log which piece failed
  onError?: (error: Error, info: ErrorInfo) => void;
  // when this changes, a failed boundary tries its children again (e.g. the
  // user switched takes, so the next take gets a fresh chance to render)
  resetKey?: unknown;
}

interface State {
  error: Error | null;
}

// The one error boundary the app uses: around the whole <App/>, around each
// annotation slot, and around the contour chart. React only offers this as a
// class component.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const { fallback } = this.props;
    return typeof fallback === "function" ? fallback(error) : fallback;
  }
}

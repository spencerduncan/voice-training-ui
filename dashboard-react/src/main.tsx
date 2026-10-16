import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";

// Last line of defence: if anything in the app throws while rendering, show a
// gentle card instead of a blank page. (Annotation slots and the contour have
// their own, smaller boundaries, so they rarely get this far.)
function Crashed({ error }: { error: Error }) {
  return (
    <div className="wrap">
      <div className="empty empty-error" role="alert">
        something in Voice Garden tripped over itself 🌧️
        <br />
        your recordings are safe — nothing was changed.
        <br />
        <span className="empty-hint">{error.message}</span>
        <br />
        <button type="button" className="crash-reload" onClick={() => window.location.reload()}>
          reload the garden
        </button>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary
      fallback={(error) => <Crashed error={error} />}
      onError={(err) => console.error("[voice garden] the page crashed", err)}
    >
      <App />
    </ErrorBoundary>
  </StrictMode>,
);

import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
// PROTOTYPE — throwaway, behind ?variant=. Four shapes for folding the planner
// into the generator. Mounted *instead of* App rather than inside it, so it
// needs no key, makes no request, and cannot disturb the real page's state.
import { MergedFlowPrototype, useVariant } from "./prototype/mergedFlow";

function Root() {
  const variant = useVariant();
  return variant ? <MergedFlowPrototype variant={variant} /> : <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);

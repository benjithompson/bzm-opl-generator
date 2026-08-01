import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// The real route caller, chosen here and nowhere else. This is the whole of
// what start-up decides: the page is handed what to call the local API with,
// so a test can hand it something else without the page knowing which it got.
import { api } from "./api";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App api={api} />
  </React.StrictMode>,
);

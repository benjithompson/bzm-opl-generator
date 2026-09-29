import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
// The real route caller; tests hand App a fake instead.
import { api } from "./api";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App api={api} />
  </React.StrictMode>,
);

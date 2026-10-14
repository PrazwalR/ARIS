import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@aris/tokens.css";
import "@aris/components/bundle.css";
import "./app.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

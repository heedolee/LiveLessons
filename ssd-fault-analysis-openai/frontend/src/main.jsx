/**
 * frontend/src/main.jsx
 * React 앱 진입점. App 컴포넌트를 #root 에 마운트합니다.
 */
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

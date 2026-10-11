import { FluentProvider } from "@fluentui/react-components";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "@fontsource/barlow-condensed/700.css";
import "@fontsource-variable/cairo";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { gtTheme } from "./theme";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <FluentProvider theme={gtTheme} className="gt-root">
      <App />
    </FluentProvider>
  </StrictMode>,
);

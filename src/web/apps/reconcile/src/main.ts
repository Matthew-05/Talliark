import { mountApp } from "./app.js";

const root = document.getElementById("app");
if (!root) throw new Error("Root #app element not found.");
mountApp(root);

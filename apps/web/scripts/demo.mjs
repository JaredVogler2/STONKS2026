#!/usr/bin/env node
// Local DEMO launcher: embedded Postgres + deterministic seed + local-only owner login. Never use on a server.
import { spawn } from "node:child_process";

const env = { ...process.env, APEX_MODE: "DEMO", APEX_ALLOW_LOCAL_DEV_LOGIN: "1" };
delete env.DATABASE_URL;
const args = process.argv.includes("--prod") ? ["start", "--port", "3000"] : ["dev", "--port", "3000"];
const child = spawn(process.platform === "win32" ? "next.cmd" : "next", args, { env, stdio: "inherit", shell: process.platform === "win32" });
child.on("exit", (code) => process.exit(code ?? 0));

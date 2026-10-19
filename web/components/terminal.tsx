/**
 * The terminal: xterm.js over a WebSocket to a real running environment.
 *
 * Every statement typed here is executed by `cqlsh` inside the learner's own
 * container, and what appears below the prompt is exactly what cqlsh printed —
 * stdout, stderr, and its errors, which the lesson deliberately provokes at one
 * point. **No output is ever synthesised in this component.** The only text it
 * writes of its own accord is the prompt, the echo of what the learner typed,
 * and a refusal the backend returned instead of running something.
 *
 * Line editing is implemented here because xterm.js is a terminal emulator, not
 * a shell: it renders bytes and reports keystrokes. Printable characters,
 * Enter, Backspace, Ctrl+C and the arrow-key history are the whole contract.
 *
 * Spec: docs/design/00-overview.md
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import "@xterm/xterm/css/xterm.css";

import { api } from "@/lib/api";
import type { CommandRecord } from "@/lib/types";

type Frame =
  | { type: "ready"; [key: string]: unknown }
  | { type: "starting"; status: string }
  | { type: "error"; message: string }
  | ({ type: "result" } & CommandRecord);

export type TerminalStatus = "connecting" | "starting" | "open" | "closed" | "error";

/**
 * A statement the lesson asked to be run.
 *
 * The nonce is what makes this one-way: the parent raises it to request a run,
 * and the terminal acts once per value. Passing a bare string would re-run on
 * every re-render, or need the parent to clear it afterwards.
 */
export type RunRequest = { statement: string; nonce: number };

export function Terminal({
  sessionId,
  prompt = "cqlsh>",
  request,
  onResult,
  onStatusChange,
}: {
  sessionId: string;
  prompt?: string;
  request?: RunRequest | null;
  onResult?: (record: CommandRecord) => void;
  onStatusChange?: (status: TerminalStatus) => void;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const term = useRef<import("@xterm/xterm").Terminal | null>(null);
  const line = useRef("");
  const historyRef = useRef<string[]>([]);
  const cursor = useRef(0);
  const busy = useRef(false);

  // Callbacks live in refs so a parent re-render never tears down the socket.
  const resultHandler = useRef(onResult);
  const statusHandler = useRef(onStatusChange);
  useEffect(() => {
    resultHandler.current = onResult;
    statusHandler.current = onStatusChange;
  }, [onResult, onStatusChange]);

  const [status, setStatus] = useState<TerminalStatus>("connecting");
  const [note, setNote] = useState<string>("");

  const report = useCallback((next: TerminalStatus) => {
    setStatus(next);
    statusHandler.current?.(next);
  }, []);

  // One effect owns the terminal and the socket, keyed only on the session, so
  // the connection survives everything else the screen does.
  useEffect(() => {
    let disposed = false;
    let instance: import("@xterm/xterm").Terminal | null = null;
    let connection: WebSocket | null = null;
    let onResize: (() => void) | null = null;

    const writePrompt = () => instance?.write(`\r\n\x1b[38;5;68m${prompt}\x1b[0m `);

    const send = (statement: string) => {
      if (!connection || connection.readyState !== WebSocket.OPEN) return;
      busy.current = true;
      historyRef.current = [...historyRef.current, statement];
      cursor.current = historyRef.current.length;
      instance?.write("\r\n");
      connection.send(JSON.stringify({ command: statement }));
    };

    const replaceLine = (next: string) => {
      if (!instance) return;
      instance.write("\r\x1b[2K");
      instance.write(`\x1b[38;5;68m${prompt}\x1b[0m ${next}`);
      line.current = next;
    };

    void (async () => {
      const [{ Terminal: XTerm }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed || !host.current) return;

      instance = new XTerm({
        convertEol: true,
        cursorBlink: true,
        fontFamily:
          'var(--font-geist-mono), ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        fontSize: 13,
        theme: { background: "#0b0b0d", foreground: "#e5e5e5", cursor: "#9ca3af" },
      });
      const fit = new FitAddon();
      instance.loadAddon(fit);
      instance.open(host.current);
      fit.fit();
      term.current = instance;

      onResize = () => fit.fit();
      window.addEventListener("resize", onResize);

      instance.onData((data) => {
        if (!instance) return;
        if (busy.current) return; // one statement at a time; the server is working

        for (const char of data) {
          const code = char.charCodeAt(0);

          if (char === "\r") {
            const statement = line.current.trim();
            line.current = "";
            if (statement) send(statement);
            else writePrompt();
          } else if (code === 127 || code === 8) {
            if (line.current.length > 0) {
              line.current = line.current.slice(0, -1);
              instance.write("\b \b");
            }
          } else if (code === 3) {
            // Ctrl+C: abandon the line, exactly as a shell would.
            instance.write("^C");
            line.current = "";
            writePrompt();
          } else if (data === "\x1b[A" || data === "\x1b[B") {
            const up = data === "\x1b[A";
            const next = Math.min(
              Math.max(cursor.current + (up ? -1 : 1), 0),
              historyRef.current.length,
            );
            cursor.current = next;
            replaceLine(historyRef.current[next] ?? "");
            break;
          } else if (code >= 32 && code !== 127) {
            line.current += char;
            instance.write(char);
          }
        }
      });

      connection = new WebSocket(api.terminalUrl(sessionId));
      socket.current = connection;

      connection.onmessage = (event: MessageEvent<string>) => {
        const frame = JSON.parse(event.data) as Frame;

        if (frame.type === "starting") {
          report("starting");
          setNote("Starting the environment — Cassandra is replaying its commit log.");
          return;
        }
        if (frame.type === "ready") {
          report("open");
          setNote("");
          instance?.write(
            "\x1b[38;5;245mConnected to a real environment. " +
              "Every statement runs inside the container.\x1b[0m",
          );
          writePrompt();
          return;
        }
        if (frame.type === "error") {
          report("error");
          setNote(frame.message);
          instance?.write(`\r\n\x1b[38;5;203m${frame.message}\x1b[0m`);
          busy.current = false;
          writePrompt();
          return;
        }

        // A result. Written verbatim; a refusal is the backend's own sentence.
        busy.current = false;
        if (frame.refused) {
          instance?.write(`\x1b[38;5;215mRefused — ${frame.refused}\x1b[0m`);
        } else {
          if (frame.stdout) instance?.write(frame.stdout.replace(/\n/g, "\r\n"));
          if (frame.stderr) {
            instance?.write(`\x1b[38;5;203m${frame.stderr.replace(/\n/g, "\r\n")}\x1b[0m`);
          }
          if (!frame.stdout && !frame.stderr) {
            instance?.write("\x1b[38;5;245m(no output)\x1b[0m");
          }
        }
        writePrompt();
        resultHandler.current?.(frame);
      };

      connection.onerror = () => {
        report("error");
        setNote("The terminal connection failed.");
      };
      connection.onclose = () => {
        if (!disposed) {
          report("closed");
          instance?.write("\r\n\x1b[38;5;245mConnection closed.\x1b[0m");
        }
      };

    })();

    return () => {
      disposed = true;
      if (onResize) window.removeEventListener("resize", onResize);
      connection?.close();
      instance?.dispose();
      socket.current = null;
      term.current = null;
    };
  }, [sessionId, prompt, report]);

  // A statement the lesson asked to run. Typed into the terminal first, so the
  // learner sees the same thing they would have typed themselves.
  const lastNonce = useRef(0);
  useEffect(() => {
    if (!request || request.nonce === lastNonce.current) return;
    if (status !== "open" || busy.current) return;
    lastNonce.current = request.nonce;

    const instance = term.current;
    const connection = socket.current;
    if (!instance || !connection || connection.readyState !== WebSocket.OPEN) return;

    instance.write(request.statement);
    instance.write("\r\n");
    busy.current = true;
    historyRef.current = [...historyRef.current, request.statement];
    cursor.current = historyRef.current.length;
    connection.send(JSON.stringify({ command: request.statement }));
  }, [request, status]);

  return (
    <div data-testid="terminal" data-status={status}>
      <div className="flex items-center gap-2 rounded-t-lg border border-neutral-800 bg-neutral-900 px-3 py-1.5">
        <span className="font-mono text-xs text-neutral-400">{prompt}</span>
        <span
          className={`ml-auto font-mono text-xs ${
            status === "open"
              ? "text-emerald-400"
              : status === "error"
                ? "text-red-400"
                : "text-neutral-500"
          }`}
          data-testid="terminal-status"
        >
          {status === "open" ? "connected" : status}
        </span>
      </div>
      <div
        ref={host}
        className="h-80 overflow-hidden rounded-b-lg border border-t-0 border-neutral-800 bg-[#0b0b0d] p-2"
      />
      {note && (
        <p className="mt-2 text-xs text-neutral-500" data-testid="terminal-note">
          {note}
        </p>
      )}
    </div>
  );
}

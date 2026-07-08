#!/usr/bin/env python3
"""Graphical front-end for the RSW robust image watermark.

A modern two-tab customtkinter interface:

* **Ocultar**   – load a carrier image, type a message (up to 512 bytes) and save
  a marked PNG whose watermark survives the resize + recompression applied by
  Facebook, WhatsApp and Pinterest.
* **Recuperar** – load any image (even one downloaded from a social network) and
  read the embedded payload back.

Heavy work (ISS embedding / detection) runs on a worker thread so the UI stays
responsive; results are marshalled back to the Tk main loop through a queue.
"""

from __future__ import annotations

import os
import queue
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from rsw.robust_watermark import MAX_PAYLOAD_BYTES, MAX_TEXT_BYTES, WatermarkConfig
from rsw.robust_watermark import embed_text as wm_embed_text
from rsw.robust_watermark import embedded_size as wm_embedded_size
from rsw.robust_watermark import extract as wm_extract

APP_TITLE = "RSW · Marca Robusta"
ACCENT = "#2fa572"
ACCENT_HOVER = "#268a5f"
# Separate entries + a real "all files" (*) escape hatch: on macOS a single
# space-separated pattern can grey out valid files, and "*.*" does not match
# extensionless files.  Listing JPEG on its own guarantees it is selectable.
IMG_EXTS = [
    ("Imágenes", "*.png *.jpg *.jpeg *.jpe *.jfif *.bmp *.tif *.tiff *.webp"),
    ("JPEG", "*.jpg *.jpeg *.jpe *.jfif"),
    ("PNG", "*.png"),
    ("Todos los archivos", "*"),
]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


class ConsoleBox(ctk.CTkTextbox):
    """A read-only, colourised log panel."""

    def __init__(self, master, **kwargs):
        super().__init__(master, state="disabled", wrap="word",
                         font=ctk.CTkFont("JetBrains Mono", 12), **kwargs)
        self.tag_config("ok", foreground="#4ade80")
        self.tag_config("err", foreground="#f87171")
        self.tag_config("info", foreground="#93c5fd")
        self.tag_config("muted", foreground="#94a3b8")

    def log(self, text: str, tag: str = "muted") -> None:
        self.configure(state="normal")
        self.insert("end", text + "\n", tag)
        self.see("end")
        self.configure(state="disabled")

    def clear(self) -> None:
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.configure(state="disabled")


class ImagePreview(ctk.CTkFrame):
    """A framed thumbnail with a caption."""

    def __init__(self, master, caption: str, size: int = 240):
        super().__init__(master, corner_radius=12)
        self._size = size
        self.label = ctk.CTkLabel(self, text="Sin imagen", width=size, height=size,
                                  fg_color=("#e5e7eb", "#1f2937"), corner_radius=10)
        self.label.pack(padx=12, pady=(12, 4))
        self.caption = ctk.CTkLabel(self, text=caption, text_color="#94a3b8",
                                    font=ctk.CTkFont(size=12))
        self.caption.pack(padx=12, pady=(0, 12))

    def show(self, path: str) -> None:
        img = Image.open(path).convert("RGB")
        img.thumbnail((self._size, self._size))
        self.label.configure(
            image=ctk.CTkImage(light_image=img, dark_image=img, size=img.size),
            text="",
        )


class WatermarkApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x720")
        self.minsize(920, 620)

        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._busy = False

        # state
        self.wm_cover_path: str | None = None
        self.verify_path: str | None = None

        self._build_header()
        self._build_tabs()
        self.after(80, self._poll_queue)

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=("#f1f5f9", "#0b1220"))
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text="RSW", font=ctk.CTkFont(size=26, weight="bold"),
            text_color=ACCENT,
        ).pack(side="left", padx=(20, 8), pady=14)
        ctk.CTkLabel(
            header, text="Marca de agua robusta · sobrevive a redes sociales (ISS + Reed-Solomon)",
            font=ctk.CTkFont(size=13), text_color="#94a3b8",
        ).pack(side="left", pady=14)

        self.mode_menu = ctk.CTkOptionMenu(
            header, values=["Dark", "Light", "System"], width=100,
            command=lambda m: ctk.set_appearance_mode(m.lower()),
        )
        self.mode_menu.set("Dark")
        self.mode_menu.pack(side="right", padx=20, pady=14)

    def _build_tabs(self) -> None:
        self.tabs = ctk.CTkTabview(self, corner_radius=12,
                                   segmented_button_selected_color=ACCENT,
                                   segmented_button_selected_hover_color=ACCENT_HOVER)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=16)
        self.tabs.add("Ocultar")
        self.tabs.add("Recuperar")
        self._build_watermark_tab(self.tabs.tab("Ocultar"))
        self._build_verify_tab(self.tabs.tab("Recuperar"))

    # -- Watermark tab -------------------------------------------------- #
    def _build_watermark_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ctk.CTkFrame(tab, corner_radius=12, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(left, text="Marca de agua · texto comprimido · sobrevive redes sociales",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=16, pady=(16, 2))
        ctk.CTkLabel(left, text="Resiste el redimensionado y la recompresión de Facebook, "
                                "WhatsApp y Pinterest. Comparte el PNG tal cual (sin recortar).",
                     text_color="#94a3b8", font=ctk.CTkFont(size=12), wraplength=420,
                     justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(fill="x", padx=16)
        ctk.CTkButton(row, text="Cargar portadora", command=self._pick_wm_cover,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(side="left")
        self.wm_cover_label = ctk.CTkLabel(row, text="Ningún archivo", text_color="#94a3b8")
        self.wm_cover_label.pack(side="left", padx=10)

        ctk.CTkLabel(left, text="Mensaje (se comprime automáticamente antes de insertar)",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=16, pady=(12, 4))
        self.wm_entry = ctk.CTkTextbox(left, height=130, wrap="word")
        self.wm_entry.pack(fill="x", padx=16, pady=6)
        self.wm_count = ctk.CTkLabel(left, text=f"0/{MAX_PAYLOAD_BYTES} B insertados",
                                     text_color="#94a3b8", font=ctk.CTkFont(size=11))
        self.wm_count.pack(anchor="e", padx=16)
        self.wm_entry.bind("<KeyRelease>", self._on_wm_entry)

        self.wm_strong = ctk.CTkCheckBox(left, text="Máxima robustez (menor calidad)",
                                         fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.wm_strong.pack(anchor="w", padx=16, pady=(8, 4))

        self.wm_button = ctk.CTkButton(
            left, text="Insertar marca", height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._run_watermark,
        )
        self.wm_button.pack(fill="x", padx=16, pady=(10, 16))

        self.wm_preview = ImagePreview(right, "Portadora")
        self.wm_preview.pack(fill="x")
        ctk.CTkLabel(right, text="Consola", text_color="#94a3b8").pack(anchor="w", pady=(8, 2))
        self.wm_console = ConsoleBox(right, height=180)
        self.wm_console.pack(fill="both", expand=True)
        self.wm_console.log("Marca resistente a redes sociales. Carga una portadora.", "info")

    # -- Verify tab ----------------------------------------------------- #
    def _build_verify_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=3)
        tab.grid_columnconfigure(1, weight=2)
        tab.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(tab, corner_radius=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = ctk.CTkFrame(tab, corner_radius=12, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(left, text="Verificar marca robusta",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=16, pady=(16, 2))
        ctk.CTkLabel(left, text="Funciona incluso tras descargar la imagen de Facebook, "
                                "WhatsApp o Pinterest.", text_color="#94a3b8",
                     font=ctk.CTkFont(size=12), wraplength=420, justify="left").pack(anchor="w", padx=16, pady=(0, 8))

        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(fill="x", padx=16)
        ctk.CTkButton(row, text="Cargar imagen", command=self._pick_verify_image,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(side="left")
        self.verify_label = ctk.CTkLabel(row, text="Ningún archivo", text_color="#94a3b8")
        self.verify_label.pack(side="left", padx=10)

        self.verify_button = ctk.CTkButton(
            left, text="Verificar marca", height=42,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._run_verify,
        )
        self.verify_button.pack(fill="x", padx=16, pady=(12, 8))

        ctk.CTkLabel(left, text="Resultado", text_color="#94a3b8").pack(anchor="w", padx=16)
        self.verify_result = ctk.CTkTextbox(left, height=180, wrap="word")
        self.verify_result.pack(fill="both", expand=True, padx=16, pady=(2, 16))

        self.verify_preview = ImagePreview(right, "Imagen a verificar")
        self.verify_preview.pack(fill="x")
        ctk.CTkLabel(right, text="Consola", text_color="#94a3b8").pack(anchor="w", pady=(8, 2))
        self.verify_console = ConsoleBox(right, height=180)
        self.verify_console.pack(fill="both", expand=True)
        self.verify_console.log("Carga una imagen para comprobar su marca.", "info")

    # ------------------------------------------------------------------ #
    # Callbacks
    # ------------------------------------------------------------------ #
    def _on_wm_entry(self, _event=None) -> None:
        value = self.wm_entry.get("1.0", "end").strip()
        raw = len(value.encode("utf-8"))
        embedded, compressed = wm_embedded_size(value) if value else (0, False)
        tag = " (comprimido)" if compressed else ""
        color = "#f87171" if embedded > MAX_PAYLOAD_BYTES else "#94a3b8"
        self.wm_count.configure(
            text=f"{raw} car → {embedded}/{MAX_PAYLOAD_BYTES} B insertados{tag}",
            text_color=color)

    def _pick_wm_cover(self) -> None:
        path = filedialog.askopenfilename(title="Selecciona la portadora", filetypes=IMG_EXTS)
        if not path:
            return
        self.wm_cover_path = path
        self.wm_cover_label.configure(text=os.path.basename(path))
        try:
            self.wm_preview.show(path)
            self.wm_console.log(f"Portadora cargada: {os.path.basename(path)}", "info")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", f"No se pudo abrir la imagen:\n{exc}")

    def _pick_verify_image(self) -> None:
        path = filedialog.askopenfilename(title="Selecciona la imagen a verificar", filetypes=IMG_EXTS)
        if not path:
            return
        self.verify_path = path
        self.verify_label.configure(text=os.path.basename(path))
        try:
            self.verify_preview.show(path)
            self.verify_console.log(f"Imagen cargada: {os.path.basename(path)}", "info")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Error", f"No se pudo abrir la imagen:\n{exc}")

    # ------------------------------------------------------------------ #
    # Workers
    # ------------------------------------------------------------------ #
    def _run_watermark(self) -> None:
        if self._busy:
            return
        if not self.wm_cover_path:
            messagebox.showwarning("Falta portadora", "Carga primero una imagen portadora.")
            return
        value = self.wm_entry.get("1.0", "end").strip()
        if not value:
            messagebox.showwarning("Sin mensaje", "Escribe el texto a insertar.")
            return
        if len(value.encode("utf-8")) > MAX_TEXT_BYTES:
            messagebox.showwarning("Texto largo", f"Máximo {MAX_TEXT_BYTES} bytes de texto.")
            return
        embedded, _ = wm_embedded_size(value)
        if embedded > MAX_PAYLOAD_BYTES:
            messagebox.showwarning(
                "Texto largo",
                f"El texto ocupa {embedded} B comprimido; el máximo es {MAX_PAYLOAD_BYTES} B.\n"
                "Acórtalo un poco (el texto repetitivo comprime mejor).")
            return

        out_path = filedialog.asksaveasfilename(
            title="Guardar imagen marcada", defaultextension=".png",
            filetypes=[("PNG (recomendado)", "*.png")], initialfile="imagen_marcada.png",
        )
        if not out_path:
            return
        cfg = WatermarkConfig.strong() if self.wm_strong.get() else WatermarkConfig()
        self._set_busy(True, "watermark")
        self.wm_console.clear()
        self.wm_console.log("Insertando marca ISS en el dominio DCT canónico…", "info")
        threading.Thread(target=self._watermark_worker, args=(value, out_path, cfg), daemon=True).start()

    def _watermark_worker(self, text, out_path, cfg) -> None:
        try:
            res = wm_embed_text(self.wm_cover_path, text, out_path, cfg)
            self._queue.put(("wm_ok", res))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("wm_err", f"{type(exc).__name__}: {exc}"))

    def _run_verify(self) -> None:
        if self._busy:
            return
        if not self.verify_path:
            messagebox.showwarning("Falta imagen", "Carga primero la imagen a verificar.")
            return
        self._set_busy(True, "verify")
        self.verify_console.clear()
        self.verify_result.delete("1.0", "end")
        self.verify_console.log("Normalizando a 1152×1152 · corrigiendo orientación · correlando ISS…", "info")
        threading.Thread(target=self._verify_worker, daemon=True).start()

    def _verify_worker(self) -> None:
        try:
            res = wm_extract(self.verify_path)
            self._queue.put(("verify_ok", res))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("verify_err", f"{type(exc).__name__}: {exc}"))

    # ------------------------------------------------------------------ #
    # Queue pump (runs on the Tk main thread)
    # ------------------------------------------------------------------ #
    def _poll_queue(self) -> None:
        try:
            while True:
                self._handle_message(self._queue.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _handle_message(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "wm_ok":
            res = msg[1]
            c = self.wm_console
            c.log("Marca robusta insertada", "ok")
            c.log(f"  Payload : {len(res.payload)} B", "muted")
            c.log(f"  PSNR    : {res.psnr_db:.2f} dB", "muted")
            c.log(f"  Guardada: {res.out_path}", "info")
            c.log("  Sobrevive a Facebook / WhatsApp / Pinterest", "ok")
            self.wm_preview.show(res.out_path)
            self._set_busy(False, "watermark")
            messagebox.showinfo("Listo", f"Imagen marcada guardada:\n{res.out_path}\n\n"
                                         f"PSNR: {res.psnr_db:.1f} dB\nEnvíala como PNG, sin recortar; "
                                         "sobrevive a las redes sociales.")
        elif kind == "wm_err":
            self.wm_console.log("Error al marcar: " + msg[1], "err")
            self._set_busy(False, "watermark")
        elif kind == "verify_ok":
            self._present_watermark(msg[1])
            self._set_busy(False, "verify")
        elif kind == "verify_err":
            self.verify_console.log("Error al verificar: " + msg[1], "err")
            self._set_busy(False, "verify")

    def _present_watermark(self, res) -> None:
        c = self.verify_console
        if not res.valid:
            c.log("No se encontró una marca válida", "err")
            c.log("Posibles causas:", "muted")
            c.log("  • la imagen no se marcó con esta versión (vuelve a marcarla)", "muted")
            c.log("  • una red social la recortó o es una captura de pantalla", "muted")
            c.log("  • (el marcado sobrevive al redimensionado, no al recorte)", "muted")
            self.verify_result.insert("1.0", "Sin marca válida.\n\nLa marca sobrevive al "
                                             "redimensionado, la recompresión y la rotación, "
                                             "pero NO al recorte ni a las capturas de pantalla.\n\n"
                                             "Vuelve a marcar la imagen con esta versión y "
                                             "compártela como PNG sin recortar.")
            return
        c.log("Marca encontrada y validada (CRC correcto)", "ok")
        c.log(f"  Payload : {len(res.payload)} B", "muted")
        text = res.text
        lines = [f"Payload: {len(res.payload)} bytes"]
        if text:
            lines.append(f"\nComo texto:\n{text}")
            preview = text if len(text) <= 60 else text[:60] + "…"
            c.log(f"  Como texto : {preview!r}", "info")
        self.verify_result.insert("1.0", "\n".join(lines))

    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool, which: str) -> None:
        self._busy = busy
        btn, label = {
            "watermark": (self.wm_button, "Insertar marca"),
            "verify": (self.verify_button, "Verificar marca"),
        }[which]
        if busy:
            btn.configure(state="disabled", text="Procesando…")
        else:
            btn.configure(state="normal", text=label)


def main() -> None:
    app = WatermarkApp()
    app.mainloop()


if __name__ == "__main__":
    main()

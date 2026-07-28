import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from tkinter import (
    Tk, Frame, Label, Entry, Button, Scale, Spinbox, StringVar,
    HORIZONTAL, filedialog, messagebox, PhotoImage,
)

DEFAULT_WIDTH = 480
DEFAULT_FPS = 10
DEFAULT_PREVIEW_FPS = 3
DEFAULT_SPEED = 1.0
PREVIEW_WIDTH = 400
PREVIEW_HEIGHT = 225

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW)


def probe_duration(path):
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "csv=p=0", path,
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="ignore"))
    return float(result.stdout.decode().strip())


def grab_frame_ppm(path, t, width=PREVIEW_WIDTH):
    t = max(0.0, t)
    result = _run([
        "ffmpeg", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
        "-vf", f"scale={width}:-1:flags=lanczos", "-f", "image2pipe",
        "-vcodec", "ppm", "-",
    ])
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def convert_to_gif(input_path, output_path, width, fps, start, end, on_status=None):
    def status(msg):
        if on_status:
            on_status(msg)

    scale_filter = f"scale={width}:-1:flags=lanczos"
    palette_path = os.path.join(tempfile.gettempdir(), f"palette_{uuid.uuid4().hex}.png")
    duration = max(0.05, end - start)
    trim_args = ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}"]

    try:
        status("팔레트 생성 중...")
        r1 = _run([
            "ffmpeg", *trim_args, "-i", input_path,
            "-vf", f"fps={fps},{scale_filter},palettegen",
            "-y", palette_path,
        ])
        if r1.returncode != 0:
            raise RuntimeError(r1.stderr.decode(errors="ignore"))

        status("GIF 인코딩 중...")
        r2 = _run([
            "ffmpeg", *trim_args, "-i", input_path, "-i", palette_path,
            "-filter_complex", f"fps={fps},{scale_filter}[x];[x][1:v]paletteuse",
            "-y", output_path,
        ])
        if r2.returncode != 0:
            raise RuntimeError(r2.stderr.decode(errors="ignore"))
    finally:
        if os.path.exists(palette_path):
            os.remove(palette_path)

    status(f"완료: {output_path}")
    return output_path


def format_time(t):
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("MP4 -> GIF 변환기")
        root.resizable(False, False)

        self.input_path = None
        self.duration = 0.0
        self.trim_start = 0.0
        self.trim_end = 0.0
        self.playing = False
        self.current_photo = None

        self.output_var = StringVar()
        self.width_var = StringVar(value=str(DEFAULT_WIDTH))
        self.fps_var = StringVar(value=str(DEFAULT_FPS))
        self.speed_var = StringVar(value=str(DEFAULT_SPEED))
        self.preview_fps_var = StringVar(value=str(DEFAULT_PREVIEW_FPS))
        self.status_var = StringVar(value="mp4 파일을 선택하세요")
        self.range_var = StringVar(value="구간: -")
        self.time_var = StringVar(value="0:00.0")

        outer = Frame(root, padx=12, pady=12)
        outer.pack()

        Button(outer, text="mp4 파일 열기", command=self.browse_input).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        self.blank_photo = self._make_blank_photo(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview_label = Label(outer, image=self.blank_photo)
        self.preview_label.grid(row=1, column=0, columnspan=4, pady=4)

        self.scale = Scale(
            outer, from_=0, to=100, orient=HORIZONTAL, length=PREVIEW_WIDTH,
            showvalue=False, command=self._on_scale_drag,
        )
        self.scale.grid(row=2, column=0, columnspan=4, sticky="we")
        self.scale.bind("<ButtonRelease-1>", self._on_scale_release)

        Label(outer, textvariable=self.time_var).grid(row=3, column=0, sticky="w")

        self.play_btn = Button(outer, text="▶ 미리보기", command=self.toggle_play, state="disabled")
        self.play_btn.grid(row=3, column=1)

        Button(outer, text="구간 시작", command=self.set_start).grid(row=3, column=2)
        Button(outer, text="구간 끝", command=self.set_end).grid(row=3, column=3)

        preview_opts = Frame(outer)
        preview_opts.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        Label(preview_opts, text="재생 속도").grid(row=0, column=0, padx=(0, 4))
        Spinbox(
            preview_opts, textvariable=self.speed_var, width=5,
            from_=0.25, to=4.0, increment=0.25, format="%.2f",
        ).grid(row=0, column=1)
        Label(preview_opts, text="x").grid(row=0, column=2, padx=(2, 12))
        Label(preview_opts, text="미리보기 FPS").grid(row=0, column=3, padx=(0, 4))
        Spinbox(
            preview_opts, textvariable=self.preview_fps_var, width=5,
            from_=1, to=15, increment=1,
        ).grid(row=0, column=4)

        Label(outer, textvariable=self.range_var).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(2, 8)
        )

        opts = Frame(outer)
        opts.grid(row=6, column=0, columnspan=4, sticky="we")
        Label(opts, text="너비(px)").grid(row=0, column=0, padx=(0, 4))
        Entry(opts, textvariable=self.width_var, width=6).grid(row=0, column=1)
        Label(opts, text="출력 FPS").grid(row=0, column=2, padx=(12, 4))
        Entry(opts, textvariable=self.fps_var, width=6).grid(row=0, column=3)

        Label(outer, text="출력 파일").grid(row=7, column=0, sticky="w", pady=(8, 0))
        Entry(outer, textvariable=self.output_var, width=42).grid(
            row=8, column=0, columnspan=3, sticky="we"
        )
        Button(outer, text="다른 이름으로", command=self.browse_output).grid(row=8, column=3)

        self.convert_btn = Button(outer, text="변환", command=self.start_convert, state="disabled")
        self.convert_btn.grid(row=9, column=0, columnspan=4, pady=(10, 0), sticky="we")

        Label(outer, textvariable=self.status_var, fg="gray20").grid(
            row=10, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

    @staticmethod
    def _make_blank_photo(width, height):
        img = PhotoImage(width=width, height=height)
        img.put("#000000", to=(0, 0, width, height))
        return img

    # ---- file selection ----

    def browse_input(self):
        path = filedialog.askopenfilename(
            title="변환할 mp4 파일 선택",
            filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            duration = probe_duration(path)
        except Exception as e:
            messagebox.showerror("오류", f"영상 정보를 읽을 수 없습니다.\n{e}")
            return

        self.input_path = path
        self.duration = duration
        self.trim_start = 0.0
        self.trim_end = duration

        base, _ = os.path.splitext(path)
        self.output_var.set(base + ".gif")

        self.scale.configure(from_=0, to=duration, resolution=0.1)
        self.scale.set(0)
        self._update_range_label()
        self.play_btn.configure(state="normal")
        self.convert_btn.configure(state="normal")
        self.status_var.set(f"로드됨: {os.path.basename(path)} ({format_time(duration)})")
        self._refresh_frame(0)

    def browse_output(self):
        path = filedialog.asksaveasfilename(
            title="GIF 저장 위치", defaultextension=".gif",
            filetypes=[("GIF files", "*.gif")],
        )
        if path:
            self.output_var.set(path)

    # ---- scrubbing / preview ----

    def _on_scale_drag(self, value):
        self.time_var.set(format_time(float(value)))

    def _on_scale_release(self, _event):
        if self.playing:
            return
        self._refresh_frame(self.scale.get())

    def _refresh_frame(self, t):
        if not self.input_path:
            return
        path = self.input_path

        def worker():
            ppm = grab_frame_ppm(path, t)
            if ppm:
                self.root.after(0, self._set_preview_image, ppm)

        threading.Thread(target=worker, daemon=True).start()

    def _set_preview_image(self, ppm_bytes):
        self.current_photo = PhotoImage(data=ppm_bytes)
        self.preview_label.configure(image=self.current_photo)

    # ---- trim range ----

    def set_start(self):
        t = self.scale.get()
        if t >= self.trim_end:
            messagebox.showwarning("구간 오류", "시작 지점은 끝 지점보다 앞서야 합니다.")
            return
        self.trim_start = t
        self._update_range_label()

    def set_end(self):
        t = self.scale.get()
        if t <= self.trim_start:
            messagebox.showwarning("구간 오류", "끝 지점은 시작 지점보다 뒤여야 합니다.")
            return
        self.trim_end = t
        self._update_range_label()

    def _update_range_label(self):
        length = self.trim_end - self.trim_start
        self.range_var.set(
            f"구간: {format_time(self.trim_start)} ~ {format_time(self.trim_end)} "
            f"(길이 {length:.1f}초)"
        )

    # ---- playback preview ----

    def toggle_play(self):
        if self.playing:
            self.playing = False
            self.play_btn.configure(text="▶ 미리보기")
            return
        if not self.input_path:
            return
        self.playing = True
        self.play_btn.configure(text="⏸ 정지")
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _play_loop(self):
        t = self.scale.get()
        last = time.time()
        while self.playing and t < self.duration:
            try:
                speed = float(self.speed_var.get())
            except ValueError:
                speed = DEFAULT_SPEED
            speed = max(0.1, min(speed, 8.0))

            try:
                preview_fps = float(self.preview_fps_var.get())
            except ValueError:
                preview_fps = DEFAULT_PREVIEW_FPS
            preview_fps = max(0.5, min(preview_fps, 15.0))
            min_interval = 1.0 / preview_fps

            ppm = grab_frame_ppm(self.input_path, t)
            if ppm:
                self.root.after(0, self._on_play_tick, ppm, t)

            # Cap the refresh rate at preview_fps, but never assume the
            # grab above was free: measure real elapsed time so "speed"
            # tracks wall-clock time consistently regardless of how long
            # frame extraction took.
            now = time.time()
            elapsed = now - last
            remaining = min_interval - elapsed
            if remaining > 0:
                time.sleep(remaining)
                elapsed = min_interval
            last = time.time()

            t += elapsed * speed
        self.root.after(0, self._stop_playback)

    def _on_play_tick(self, ppm, t):
        if not self.playing:
            return
        self.current_photo = PhotoImage(data=ppm)
        self.preview_label.configure(image=self.current_photo)
        self.scale.set(t)
        self.time_var.set(format_time(t))

    def _stop_playback(self):
        self.playing = False
        self.play_btn.configure(text="▶ 미리보기")

    # ---- conversion ----

    def start_convert(self):
        if not self.input_path:
            return
        output_path = self.output_var.get().strip()
        if not output_path:
            messagebox.showerror("오류", "출력 파일 경로를 입력하세요.")
            return
        try:
            width = int(self.width_var.get())
            fps = int(self.fps_var.get())
        except ValueError:
            messagebox.showerror("오류", "너비와 FPS는 숫자로 입력하세요.")
            return
        if self.trim_end <= self.trim_start:
            messagebox.showerror("오류", "구간(시작~끝)을 올바르게 설정하세요.")
            return

        self.playing = False
        self.convert_btn.configure(state="disabled")
        self.status_var.set("변환 시작...")

        start, end = self.trim_start, self.trim_end
        input_path = self.input_path

        def worker():
            try:
                convert_to_gif(
                    input_path, output_path, width, fps, start, end,
                    on_status=lambda msg: self.root.after(0, self.status_var.set, msg),
                )
                self.root.after(0, self._on_success, output_path)
            except FileNotFoundError:
                self.root.after(
                    0, self._on_error,
                    "ffmpeg를 찾을 수 없습니다. PATH에 ffmpeg가 설치되어 있는지 확인하세요.",
                )
            except Exception as e:
                self.root.after(0, self._on_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, output_path):
        self.convert_btn.configure(state="normal")
        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        self.status_var.set(f"완료: {output_path} ({size_mb:.2f} MB)")
        messagebox.showinfo("완료", f"GIF 생성 완료\n{output_path}\n{size_mb:.2f} MB")

    def _on_error(self, message):
        self.convert_btn.configure(state="normal")
        self.status_var.set("오류 발생")
        messagebox.showerror("변환 실패", message)


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()

export default {
  props: { video: Number, background: String, labels: Object },
  template: `
    <div class="column full-width q-gutter-y-sm">
      <div class="row items-center q-gutter-sm">
        <q-btn :label="stream ? labels.off : labels.test" :icon="stream ? 'mic_off' : 'mic'"
          outline no-caps color="primary" :disable="Boolean(phase)" @click="toggleMic" />
        <label class="column">
          <span>{{ labels.mic }}</span>
          <select v-model="device" :disabled="Boolean(phase) || !devices.length" @change="changeDevice">
            <option v-for="(mic, index) in devices" :key="mic.deviceId" :value="mic.deviceId">
              {{ mic.label || labels.mic + ' ' + (index + 1) }}
            </option>
          </select>
        </label>
        <label class="column">
          <span>{{ labels.guide }}</span>
          <select v-model="guide" :disabled="Boolean(phase)">
            <option value="mute">{{ labels.mute }}</option>
            <option value="original">{{ labels.original }}</option>
            <option value="background">{{ labels.background }}</option>
          </select>
        </label>
      </div>
      <div class="text-caption">{{ labels.hint }}</div>
      <div class="row items-center q-gutter-sm">
        <span>{{ labels.level }}</span>
        <div role="meter" :aria-label="labels.level" :aria-valuenow="Math.round(level * 100)"
          aria-valuemin="0" aria-valuemax="100"
          style="width:min(220px,55vw);height:10px;background:#e4ecee;border-radius:999px;overflow:hidden">
          <div :style="{width:(level*100)+'%',background:level>0.88?'#d83238':level>0.64?'#df9d20':'#168774'}"
            style="height:100%;transition:width .09s linear"></div>
        </div>
      </div>
      <div v-if="phase === 'count' || phase === 'recording'" class="q-pa-sm rounded-borders bg-blue-grey-1">
        <strong>{{ phase === 'count' ? labels.countdown + ' ' + count : labels.recording + ' ' + remaining.toFixed(2) + ' s' }}</strong>
        <q-linear-progress v-if="phase === 'recording'" :value="cue" color="primary" rounded class="q-my-sm" />
        <q-btn :label="labels.cancel" icon="close" flat no-caps @click="cancel" />
      </div>
      <div v-if="status" role="status">{{ status }}</div>
    </div>`,
  data() {
    return { devices: [], device: "", guide: "mute", stream: null, context: null, level: 0,
      phase: "", count: 3, cue: 0, remaining: 0, status: "", frame: null, run: null, destroyed: false };
  },
  mounted() {
    navigator.mediaDevices?.addEventListener?.("devicechange", this.listDevices);
    this.listDevices();
  },
  beforeUnmount() {
    this.destroyed = true;
    navigator.mediaDevices?.removeEventListener?.("devicechange", this.listDevices);
    this.cancel();
    this.closeMic();
  },
  methods: {
    clearStatus() {
      this.status = "";
    },
    async listDevices() {
      try {
        this.devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === "audioinput");
        if (!this.devices.some(device => device.deviceId === this.device)) this.device = this.devices[0]?.deviceId || "";
      } catch { this.devices = []; }
    },
    async openMic() {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder || !Audio) {
        this.status = this.labels.unsupported;
        return false;
      }
      try {
        this.context ||= new Audio();
        await this.context.resume();
        if (this.stream?.getAudioTracks().some(track => track.readyState === "live")) return true;
        const options = {echoCancellation: true, noiseSuppression: false, autoGainControl: false};
        if (this.device) options.deviceId = {exact: this.device};
        const stream = await navigator.mediaDevices.getUserMedia({audio: options});
        if (this.destroyed) {
          stream.getTracks().forEach(track => track.stop());
          return false;
        }
        this.stream = stream;
        await this.listDevices();
        const analyser = this.context.createAnalyser();
        const input = this.context.createMediaStreamSource(stream);
        input.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const read = () => {
          if (this.stream !== stream) { input.disconnect(); return; }
          analyser.getFloatTimeDomainData(samples);
          let peak = 0;
          for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
          this.level = Math.max(peak, this.level * 0.83);
          this.frame = requestAnimationFrame(read);
        };
        read();
        this.status = "";
        return true;
      } catch (error) {
        this.status = error.name === "NotAllowedError" ? this.labels.denied
          : ["NotFoundError", "OverconstrainedError"].includes(error.name) ? this.labels.missing : this.labels.failed;
        return false;
      }
    },
    closeMic() {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.frame = null;
      this.stream?.getTracks().forEach(track => track.stop());
      this.stream = null;
      this.level = 0;
      this.context?.close().catch(() => {});
      this.context = null;
    },
    toggleMic() {
      if (this.stream) this.closeMic();
      else this.openMic();
    },
    async changeDevice() {
      if (!this.stream) return;
      this.closeMic();
      await this.openMic();
    },
    beep() {
      const context = this.context;
      if (!context || context.state !== "running") return;
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const now = context.currentTime;
      oscillator.frequency.value = 850;
      gain.gain.setValueAtTime(0.11, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.10);
      oscillator.connect(gain).connect(context.destination);
      oscillator.start(now);
      oscillator.stop(now + 0.11);
    },
    async record(start, end, url) {
      if (this.phase) return;
      const root = getHtmlElement(this.video);
      const video = root?.matches("video") ? root : root?.querySelector("video");
      if (!video || !Number.isFinite(video.duration) || end > video.duration + 0.005) {
        this.status = this.labels.player;
        return;
      }
      if (this.guide === "background" && !this.background) {
        this.status = this.labels.guideMissing;
        return;
      }
      this.phase = "opening";
      this.$emit("busy", true);
      if (!(await this.openMic())) {
        this.phase = "";
        this.$emit("busy", false);
        return;
      }
      const background = this.guide === "background" ? new Audio(this.background) : null;
      this.run = {
        video, background, volume: video.volume, muted: video.muted, controls: video.controls, cancelled: false,
      };
      const job = this.run;
      try {
        video.pause();
        const from = Math.max(0, start - 3);
        await this.seekVideo(video, from);
        if (job.cancelled) return;
        video.controls = false;
        job.interrupt = () => {
          this.status = this.labels.failed;
          this.cancel();
        };
        video.addEventListener("seeking", job.interrupt);
        video.muted = this.guide !== "original";
        if (!video.muted) video.volume = 0.20;
        if (background) {
          background.volume = 0.75;
          background.currentTime = from;
        }
        const choices = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
        const mime = choices.find(type => MediaRecorder.isTypeSupported(type));
        const recorder = mime ? new MediaRecorder(this.stream, {mimeType:mime}) : new MediaRecorder(this.stream);
        job.recorder = recorder;
        const chunks = [];
        recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
        recorder.onerror = () => { this.status = this.labels.failed; this.cancel(); };
        recorder.onstop = async () => {
          if (job.cancelled) return;
          this.phase = "saving";
          this.status = this.labels.saving;
          try {
            const blob = new Blob(chunks, {type: recorder.mimeType || chunks[0]?.type || "audio/webm"});
            const response = await fetch(url + "?capture_offset=" + job.offset, {method:"POST", body:blob});
            const result = await response.json();
            if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : this.labels.failed);
            this.status = this.labels.ready;
            this.$emit("saved");
          } catch (error) { this.status = error.message || this.labels.failed; }
          finally { this.finish(job); }
        };
        this.phase = "count";
        this.count = 3;
        const extraLead = Math.max(0, 3 - start);
        const beginning = performance.now();
        let playing = false;
        let lastCount = 4;
        let lastFrame = beginning;
        let lastPlayback = beginning;
        let previousPosition = from;
        const advance = now => {
          if (job.cancelled) return;
          try {
            if (now - lastFrame > 4500) throw new Error(this.labels.failed);
            lastFrame = now;
            const pausedTime = Math.min(extraLead, (now - beginning) / 1000);
            if (!playing && pausedTime >= extraLead) {
              playing = true;
              video.play().catch(() => { this.status = this.labels.failed; this.cancel(); });
              background?.play().catch(() => { this.status = this.labels.failed; this.cancel(); });
            }
            const position = video.currentTime;
            if (position > previousPosition || !playing) lastPlayback = now;
            previousPosition = position;
            if (now - lastPlayback > 4500) throw new Error(this.labels.failed);
            if (background && playing && Math.abs(background.currentTime - position) > 0.25) background.currentTime = position;
            const until = start - position + (playing ? 0 : extraLead - pausedTime);
            if (this.phase === "count") {
              const current = Math.max(1, Math.min(3, Math.ceil(until)));
              if (current < lastCount) { lastCount = current; this.count = current; this.beep(); }
              if (playing && position >= start) {
                job.offset = Math.min(1000, Math.round((position - start) * 1000));
                recorder.start();
                this.phase = "recording";
              }
            }
            if (this.phase === "recording") {
              this.remaining = Math.max(0, end - position);
              this.cue = Math.min(1, this.remaining / (end - start));
              if (position >= end || video.ended) {
                video.pause();
                background?.pause();
                recorder.stop();
                return;
              }
            }
            if (video.ended) throw new Error(this.labels.failed);
            job.frame = requestAnimationFrame(advance);
          } catch (error) { this.status = error.message || this.labels.failed; this.cancel(); }
        };
        job.frame = requestAnimationFrame(advance);
      } catch {
        this.status = this.labels.failed;
        this.cancel();
      }
    },
    seekVideo(video, seconds) {
      if (!video.seeking && Math.abs(video.currentTime - seconds) < 0.005) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const done = () => { clearTimeout(timeout); resolve(); };
        const timeout = setTimeout(() => {
          video.removeEventListener("seeked", done);
          reject(new Error(this.labels.player));
        }, 5000);
        video.addEventListener("seeked", done, {once:true});
        video.currentTime = seconds;
      });
    },
    finish(job) {
      if (!job || this.run !== job) return;
      cancelAnimationFrame(job.frame);
      job.video.pause();
      job.video.volume = job.volume;
      job.video.muted = job.muted;
      job.video.controls = job.controls;
      if (job.interrupt) job.video.removeEventListener("seeking", job.interrupt);
      job.background?.pause();
      this.run = null;
      this.phase = "";
      if (!this.destroyed) this.$emit("busy", false);
    },
    cancel() {
      const job = this.run;
      if (!job) return;
      job.cancelled = true;
      if (job.recorder?.state === "recording") job.recorder.stop();
      this.finish(job);
    },
  },
};

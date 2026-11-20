export default {
  template: `
    <div class="column q-gutter-sm">
      <div class="row items-center q-gutter-sm">
        <q-btn v-if="!recording" icon="mic" color="primary" no-caps
          :label="labels.start" :disable="requesting || sending" @click="start" />
        <q-btn v-else icon="stop" color="negative" no-caps :label="labels.stop" @click="stop" />
        <span v-if="recording" role="status">{{ labels.recording }} {{ seconds }} / 30 s</span>
      </div>
      <div class="text-caption">{{ labels.hint }}</div>
      <audio v-if="preview" :src="preview" controls class="full-width" />
      <q-btn v-if="preview" icon="check" color="primary" outline no-caps
        :label="labels.use" :loading="sending" :disable="used || recording || requesting" @click="use" />
      <div role="status" aria-live="polite" :class="error ? 'text-negative' : 'text-grey-8'">{{ message }}</div>
    </div>`,
  props: { labels: Object, stopSpeech: Boolean },
  data() {
    return { recording: false, requesting: false, sending: false, used: false, seconds: 0,
      preview: '', message: '', error: false, recorder: null, stream: null,
      timer: null, blob: null, disposed: false };
  },
  beforeUnmount() {
    this.disposed = true;
    this.stop();
    this.release();
    if (this.preview) URL.revokeObjectURL(this.preview);
  },
  methods: {
    release() {
      clearInterval(this.timer);
      this.timer = null;
      this.stream?.getTracks().forEach(track => track.stop());
      this.stream = null;
    },
    async start() {
      if (this.requesting || this.recording || this.sending) return;
      if (this.stopSpeech) window.speechSynthesis?.cancel();
      this.requesting = true;
      this.error = false;
      this.message = this.labels.permission;
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
          throw new Error(this.labels.unsupported);
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (this.disposed) { stream.getTracks().forEach(track => track.stop()); return; }
        this.stream = stream;
        const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4']
          .find(type => MediaRecorder.isTypeSupported(type));
        const recorder = new MediaRecorder(stream, { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: 64000 });
        this.recorder = recorder;
        const chunks = [];
        recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
        recorder.onerror = () => {
          this.error = true;
          this.message = this.labels.failed;
          this.stop();
          this.release();
        };
        recorder.onstop = () => {
          this.recording = false;
          this.release();
          if (this.disposed || this.error) return;
          this.blob = new Blob(chunks, { type: recorder.mimeType });
          if (this.preview) URL.revokeObjectURL(this.preview);
          this.preview = URL.createObjectURL(this.blob);
          this.message = this.labels.preview;
        };
        recorder.start();
        if (this.preview) URL.revokeObjectURL(this.preview);
        this.preview = '';
        this.blob = null;
        this.used = false;
        this.seconds = 0;
        this.recording = true;
        this.message = this.labels.active;
        const started = performance.now();
        this.timer = setInterval(() => {
          this.seconds = Math.min(30, Math.floor((performance.now() - started) / 1000));
          if (performance.now() - started >= 30000) this.stop();
        }, 100);
      } catch (error) {
        this.release();
        this.error = true;
        const messages = {
          NotAllowedError: this.labels.denied,
          NotFoundError: this.labels.missing,
          NotReadableError: this.labels.busy,
        };
        this.message = messages[error.name] || error.message || this.labels.open_failed;
      } finally { this.requesting = false; }
    },
    stop() {
      if (this.recorder?.state === 'recording') this.recorder.stop();
      this.recording = false;
      this.release();
    },
    async use() {
      if (!this.blob || this.sending || this.used) return;
      if (!this.blob.size || this.blob.size > 600000) {
        this.error = true;
        this.message = this.labels.too_large;
        return;
      }
      this.sending = true;
      this.message = this.labels.preparing;
      try {
        const data = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result.split(',')[1]);
          reader.onerror = reject;
          reader.readAsDataURL(this.blob);
        });
        this.$emit('recorded', data);
      } catch {
        this.finish(false);
      }
    },
    finish(success) {
      this.sending = false;
      this.used = success;
      this.error = !success;
      this.message = success ? this.labels.selected : this.labels.retry;
    },
  },
};

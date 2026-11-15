export default {
  template: `
    <div class="column q-gutter-sm">
      <div class="row items-center q-gutter-sm">
        <q-btn v-if="!recording" icon="mic" color="primary" no-caps
          label="Gravar pelo microfone" :disable="requesting || sending" @click="start" />
        <q-btn v-else icon="stop" color="negative" no-caps label="Parar gravação" @click="stop" />
        <span v-if="recording" role="status">Gravando: {{ seconds }} / 30 s</span>
      </div>
      <div class="text-caption">Fale de 1 a 30 segundos. Prefira até 12 segundos, em um ambiente sem música.</div>
      <audio v-if="preview" :src="preview" controls class="full-width" />
      <q-btn v-if="preview" icon="check" color="primary" outline no-caps
        label="Usar esta gravação" :loading="sending" :disable="used || recording || requesting" @click="use" />
      <div role="status" aria-live="polite" :class="error ? 'text-negative' : 'text-grey-8'">{{ message }}</div>
    </div>`,
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
      this.requesting = true;
      this.error = false;
      this.message = 'Permita o uso do microfone no navegador.';
      try {
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
          throw new Error('Use um navegador atualizado e abra o Studio em localhost ou 127.0.0.1.');
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
          this.message = 'Não foi possível concluir a gravação. Tente novamente.';
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
          this.message = 'Ouça e clique em “Usar esta gravação”, ou grave novamente.';
        };
        recorder.start();
        if (this.preview) URL.revokeObjectURL(this.preview);
        this.preview = '';
        this.blob = null;
        this.used = false;
        this.seconds = 0;
        this.recording = true;
        this.message = 'Microfone ligado.';
        const started = performance.now();
        this.timer = setInterval(() => {
          this.seconds = Math.min(30, Math.floor((performance.now() - started) / 1000));
          if (performance.now() - started >= 30000) this.stop();
        }, 100);
      } catch (error) {
        this.release();
        this.error = true;
        const messages = {
          NotAllowedError: 'Acesso ao microfone negado. Permita o microfone nas configurações deste site no navegador.',
          NotFoundError: 'Nenhum microfone encontrado. Conecte um microfone e tente novamente.',
          NotReadableError: 'O microfone está indisponível ou em uso por outro programa.',
        };
        this.message = messages[error.name] || error.message || 'Não foi possível abrir o microfone.';
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
        this.message = 'Grave um trecho mais curto para usar como referência.';
        return;
      }
      this.sending = true;
      this.message = 'Preparando referência…';
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
      this.message = success ? 'Gravação selecionada como referência.' : 'Não foi possível usar a gravação. Grave de 1 a 30 segundos e tente novamente.';
    },
  },
};

export default {
  template: '<span hidden></span>',
  data() {
    return { playbackId: 0, utterance: null };
  },
  beforeUnmount() {
    this.stop();
  },
  methods: {
    stop() {
      this.playbackId++;
      if (this.utterance) {
        this.utterance = null;
        window.speechSynthesis?.cancel();
      }
    },
    speak(text, locale) {
      const synthesis = window.speechSynthesis;
      if (!synthesis || !window.SpeechSynthesisUtterance) {
        this.$emit('unavailable');
        return;
      }
      this.stop();
      const language = locale.toLowerCase();
      const voices = synthesis.getVoices();
      const voice = voices.find(item => item.localService && item.lang.toLowerCase() === language)
        || voices.find(item => item.lang.toLowerCase() === language)
        || voices.find(item => item.lang.toLowerCase().split('-')[0] === language.split('-')[0]);
      const sentences = text.match(/[^.!?。！？\n]+[.!?。！？]*|[.!?。！？]+/gu) || [text];
      const chunks = sentences.flatMap(sentence => sentence.trim().match(/.{1,220}(?:\s|$)|.{1,220}/gu) || []);
      const playbackId = this.playbackId;
      let index = 0;
      const next = () => {
        if (this.playbackId !== playbackId) return;
        if (index >= chunks.length) {
          this.utterance = null;
          return;
        }
        const utterance = new SpeechSynthesisUtterance(chunks[index++].trim());
        utterance.lang = voice?.lang || locale;
        if (voice) utterance.voice = voice;
        utterance.onend = next;
        utterance.onerror = event => {
          if (this.playbackId !== playbackId) return;
          this.utterance = null;
          if (event.error !== 'canceled' && event.error !== 'interrupted') this.$emit('unavailable');
        };
        this.utterance = utterance;
        synthesis.speak(utterance);
      };
      next();
    },
  },
};

export default {
  props: {
    src: String, peaks: Array, duration: Number, trimmable: Boolean, trimming: Boolean, labels: Object,
  },
  template: `
    <div v-if="src" class="column full-width q-gutter-y-sm">
      <audio ref="audio" :src="src" controls preload="metadata" class="full-width"
        @loadedmetadata="loadDuration" @durationchange="loadDuration" @timeupdate="syncTime"
        @play="trackPlayback" @pause="syncTime" @ended="stopPreview" />
      <div v-if="length > 0">
        <div class="text-right text-caption">{{ formatTime(position, true) }} / {{ formatTime(length, true) }}</div>
        <div ref="track" class="relative-position rounded-borders bg-blue-grey-1"
          style="height: 62px; cursor: pointer; touch-action: none;">
          <div v-if="canTrim" class="absolute-top-left" aria-hidden="true"
            :style="{ left: percent(start), width: percent(end - start), height: '100%', background: '#99f6e488' }" />
          <div aria-hidden="true" class="row no-wrap items-center absolute-full"
            style="pointer-events: none; overflow: hidden;">
            <div v-for="(peak, index) in peaks" :key="index"
              :style="{ flex: '1 1 0', height: Math.max(3, peak * 49) + 'px',
                background: !canTrim || (index / peaks.length * length >= start
                  && index / peaks.length * length <= end) ? '#137d80' : '#8da3ab' }" />
          </div>
          <div aria-hidden="true" class="absolute-top-left"
            :style="{ left: percent(position), height: '100%', width: '2px', background: '#102e38', pointerEvents: 'none' }" />
          <div role="slider" tabindex="0" :aria-label="labels.timeline" class="absolute-full"
            :aria-valuemin="0" :aria-valuemax="length" :aria-valuenow="Number(Math.min(position, length).toFixed(2))"
            :aria-valuetext="formatTime(position, true)"
            @pointerdown="beginDrag($event, 'position')" @pointermove="moveDrag"
            @lostpointercapture="drag = null" @keydown="keyboard($event, 'position')" />
          <template v-if="canTrim">
            <button v-for="edge in ['start', 'end']" :key="edge" type="button" role="slider"
              :aria-label="labels[edge]" :aria-valuemin="edge === 'start' ? 0 : 1"
              :aria-valuemax="edge === 'start' ? length - 1 : length"
              :aria-valuenow="edge === 'start' ? start : end" :aria-valuetext="formatTime(edge === 'start' ? start : end, true)"
              :disabled="trimming" class="absolute-top-left rounded-borders text-white"
              :style="{ left: percent(edge === 'start' ? start : end), transform: 'translateX(-50%)',
                width: '15px', height: '100%', border: '1px solid white', background: '#126367', cursor: 'ew-resize' }"
              @pointerdown.stop="beginDrag($event, edge)" @pointermove.stop="moveDrag"
              @lostpointercapture.stop="drag = null" @keydown.stop="keyboard($event, edge)">⋮</button>
          </template>
        </div>
        <div class="row justify-between text-caption q-mt-xs" aria-hidden="true">
          <span>0:00</span>
          <span v-if="length >= 4">{{ formatTime(length / 2) }}</span>
          <span>{{ formatTime(length) }}</span>
        </div>
      </div>
      <template v-if="canTrim">
        <div class="text-caption">{{ labels.hint }}</div>
        <div class="row q-gutter-x-md text-caption">
          <span>{{ labels.start }} {{ formatTime(start, true) }}</span>
          <span>{{ labels.end }} {{ formatTime(end, true) }}</span>
          <span>{{ labels.selection }} {{ formatTime(end - start, true) }}</span>
        </div>
        <div class="row q-gutter-sm">
          <q-btn :icon="previewing ? 'stop' : 'play_arrow'" outline no-caps color="primary"
            :label="previewing ? labels.stop : labels.preview" @click="togglePreview" />
          <q-btn icon="content_cut" unelevated no-caps color="primary" :label="labels.apply"
            :loading="trimming" @click="$emit('trim', { src, start, end })" />
        </div>
      </template>
    </div>`,
  data() {
    return { mediaLength: 0, position: 0, start: 0, end: 0, drag: null, frame: null, previewing: false };
  },
  computed: {
    length() { return this.duration || this.mediaLength; },
    canTrim() { return this.trimmable && this.duration >= 1; },
  },
  watch: {
    src() {
      this.stopPreview();
      this.mediaLength = 0;
      this.position = 0;
      this.resetSelection();
    },
    duration() { this.resetSelection(); },
  },
  beforeUnmount() { this.stopPreview(); },
  methods: {
    resetSelection() {
      this.start = 0;
      this.end = Math.min(12, this.duration);
    },
    loadDuration() {
      const length = this.$refs.audio?.duration;
      this.mediaLength = Number.isFinite(length) ? length : 0;
      this.syncTime();
    },
    formatTime(seconds, precision = false) {
      const centiseconds = Math.max(0, Math.round(seconds * 100));
      const minutes = Math.floor(centiseconds / 6000);
      const secs = String(Math.floor(centiseconds / 100) % 60).padStart(2, '0');
      return minutes + ':' + secs + (precision ? '.' + String(centiseconds % 100).padStart(2, '0') : '');
    },
    percent(seconds) {
      return (this.length ? Math.min(100, 100 * seconds / this.length) : 0) + '%';
    },
    syncTime() {
      const audio = this.$refs.audio;
      if (audio) this.position = audio.currentTime;
    },
    seek(seconds) {
      const audio = this.$refs.audio;
      if (!audio || !Number.isFinite(audio.duration)) return;
      audio.currentTime = Math.min(audio.duration, Math.max(0, seconds));
      this.syncTime();
    },
    move(target, time) {
      if (target === 'position') {
        if (this.previewing) this.stopPreview();
        this.seek(Math.max(0, Math.min(this.length, time)));
        return;
      }
      if (this.trimming) return;
      const rounded = Math.round(time * 100) / 100;
      if (target === 'start') {
        this.start = Math.max(0, Math.min(this.duration - 1, rounded));
        this.end = Math.min(this.duration, Math.max(this.start + 1, Math.min(this.end, this.start + 30)));
      } else {
        this.end = Math.min(this.duration, Math.max(1, rounded));
        this.start = Math.max(0, Math.min(this.end - 1, Math.max(this.start, this.end - 30)));
      }
      if (this.previewing) this.stopPreview();
      this.seek(target === 'start' ? this.start : this.end);
    },
    beginDrag(event, target) {
      if (event.button !== 0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      this.drag = target;
      if (target === 'position') this.moveDrag(event);
    },
    moveDrag(event) {
      if (!this.drag) return;
      const track = this.$refs.track.getBoundingClientRect();
      this.move(this.drag, Math.max(0, Math.min(1, (event.clientX - track.left) / track.width)) * this.length);
    },
    keyboard(event, target) {
      const step = event.shiftKey ? 1 : 0.1;
      const time = this[target];
      const positions = { ArrowLeft: time - step, ArrowRight: time + step, Home: 0, End: this.length };
      if (!(event.key in positions)) return;
      event.preventDefault();
      this.move(target, positions[event.key]);
    },
    trackPlayback() {
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      const tick = () => {
        const audio = this.$refs.audio;
        this.syncTime();
        if (audio && this.previewing && audio.currentTime >= this.end) {
          this.stopPreview();
          this.seek(this.end);
          return;
        }
        this.frame = audio && !audio.paused ? requestAnimationFrame(tick) : null;
        if (this.frame === null) this.previewing = false;
      };
      tick();
    },
    stopPreview() {
      if (this.previewing) this.$refs.audio?.pause();
      this.previewing = false;
      if (this.frame !== null) cancelAnimationFrame(this.frame);
      this.frame = null;
    },
    async togglePreview() {
      if (this.previewing) {
        this.stopPreview();
        return;
      }
      const audio = this.$refs.audio;
      this.seek(this.start);
      this.previewing = true;
      try {
        await audio.play();
        this.trackPlayback();
      } catch {
        this.stopPreview();
      }
    },
  },
};

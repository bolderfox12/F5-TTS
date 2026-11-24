export default {
  props: {
    original: Number, dubbed: Number, originalTracks: Array, dubbedTracks: Array, labels: Object,
    rangeEnabled: Boolean, rangeStart: Number, rangeEnd: Number,
  },
  template: `
    <section class="full-width" :aria-label="labels.timeline"
      style="box-sizing:border-box;background:#102632;color:#ecfbfb;border:1px solid #294955;border-radius:16px;padding:14px;box-shadow:0 10px 24px #10263220">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px">
        <strong>{{ labels.timeline }}</strong>
        <div v-if="dubbed" style="display:flex;gap:4px;padding:3px;background:#1c3744;border-radius:9px">
          <button v-for="key in ['original','dubbed']" :key="key" type="button"
            :aria-pressed="active === key" @click="select(key)"
            :style="{border:0,cursor:'pointer',borderRadius:'6px',padding:'5px 9px',
              background:active === key ? '#a4efe3' : 'transparent',color:active === key ? '#112d35' : '#eefbfc'}">
            {{ labels[key] }}
          </button>
        </div>
        <span style="color:#a9dee0;font-variant-numeric:tabular-nums">{{ clock(position) }} / {{ clock(duration) }}</span>
      </div>
      <div v-if="duration" style="display:grid;grid-template-columns:clamp(62px,13vw,110px) minmax(0,1fr);gap:8px 10px">
        <div></div>
        <div style="display:flex;justify-content:space-between;color:#abc1cb;font-size:11px;font-variant-numeric:tabular-nums">
          <span v-for="index in tickCount + 1" :key="index">{{ clock((index - 1) * duration / tickCount, false) }}</span>
        </div>
        <div></div>
        <input type="range" :min="0" :max="duration" step="any" :value="position" :aria-label="labels.timeline"
          :aria-valuetext="clock(position)" @input="seek(Number($event.target.value))"
          style="width:100%;margin:0;accent-color:#75ead8;cursor:pointer" />
        <template v-if="rangeEnabled">
          <span style="align-self:center;font-size:12px;color:#d1e6ed">{{ labels.dubRange }}</span>
          <div ref="rangeTrack" @pointerdown="beginRange('selection',$event)"
            @pointermove="moveRange($event)" @pointerup="endRange($event)" @pointercancel="endRange($event)"
            style="height:38px;position:relative;min-width:0;cursor:crosshair;touch-action:none;overflow:hidden;
              border:1px solid #284957;border-radius:6px;background:repeating-linear-gradient(to right,#172f3d 0,#172f3d calc(20% - 1px),#284957 calc(20% - 1px),#284957 20%)">
            <span :style="{left:percent(activeRangeStart),width:percent(activeRangeEnd-activeRangeStart)}"
              style="position:absolute;top:4px;bottom:4px;background:#63d9c688;border:1px solid #75ead8;border-radius:4px;box-sizing:border-box;pointer-events:none"></span>
            <button type="button" :aria-label="labels.dubRange+' · '+clock(activeRangeStart)"
              @pointerdown.stop="beginRange('start',$event)"
              :style="{left:percent(activeRangeStart)}"
              style="position:absolute;top:0;bottom:0;width:14px;transform:translateX(-50%);padding:0;border:0;border-radius:4px;background:#a4efe3;cursor:ew-resize;touch-action:none;z-index:2"></button>
            <button type="button" :aria-label="labels.dubRange+' · '+clock(activeRangeEnd)"
              @pointerdown.stop="beginRange('end',$event)"
              :style="{left:percent(activeRangeEnd)}"
              style="position:absolute;top:0;bottom:0;width:14px;transform:translateX(-50%);padding:0;border:0;border-radius:4px;background:#a4efe3;cursor:ew-resize;touch-action:none;z-index:2"></button>
            <span aria-hidden="true" :style="{left:percent(position)}"
              style="position:absolute;top:0;bottom:0;width:2px;background:#f2fffe;box-shadow:0 0 5px #5aeedb;pointer-events:none;z-index:1"></span>
          </div>
          <span style="grid-column:2;color:#a9dee0;font-size:11px">
            {{ clock(activeRangeStart) }} – {{ clock(activeRangeEnd) }} · {{ labels.rangeHint }}
          </span>
        </template>
        <div style="display:grid;gap:5px;grid-column:1/-1;grid-template-columns:subgrid;position:relative">
          <template v-for="track in tracks" :key="track.name">
            <span style="align-self:center;font-size:12px;color:#d1e6ed;overflow-wrap:anywhere">{{ track.name }}</span>
            <div @pointerdown="seekFromTrack" style="height:33px;position:relative;min-width:0;cursor:pointer;overflow:hidden;
              border:1px solid #284957;border-radius:6px;background:repeating-linear-gradient(to right,#172f3d 0,#172f3d calc(20% - 1px),#284957 calc(20% - 1px),#284957 20%)">
              <span v-for="(block,index) in track.blocks" :key="index" :title="block.label"
                :style="{ left: percent(block.start), width: percent(block.end - block.start), background: colors[track.kind] }"
                style="position:absolute;top:3px;bottom:3px;min-width:2px;box-sizing:border-box;border-radius:4px;color:#0e2832;
                  font-size:11px;line-height:25px;font-weight:650;padding:0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                {{ block.label }}
              </span>
              <span aria-hidden="true" :style="{left:percent(position)}"
                style="position:absolute;top:0;bottom:0;width:2px;background:#f2fffe;box-shadow:0 0 5px #5aeedb;pointer-events:none"></span>
            </div>
          </template>
        </div>
      </div>
    </section>`,
  data() {
    return {
      active: this.dubbed ? "dubbed" : "original", duration: 0, position: 0, media: {},
      rangeDrag: null, draftRangeStart: 0, draftRangeEnd: 0,
      colors: { video: "#63d9c6", voice: "#7ebaf0", captions: "#ffd481", music: "#cbb2f7" },
    };
  },
  computed: {
    activeRangeStart() {
      return this.rangeDrag ? this.draftRangeStart : this.rangeStart == null ? 0 : this.rangeStart;
    },
    activeRangeEnd() {
      return this.rangeDrag ? this.draftRangeEnd : this.rangeEnd == null ? this.duration : this.rangeEnd;
    },
    tracks() {
      const specs = this.active === "dubbed" ? this.dubbedTracks : this.originalTracks;
      return [
        {name: this.labels.video, kind: "video", blocks: [{start: 0, end: this.duration, label: this.labels[this.active]}]},
      ].map(track => specs?.some(item => item.kind === "video") ? null : track)
        .filter(Boolean).concat(specs || []);
    },
    tickCount() {
      return this.duration < 8 ? 2 : 4;
    },
  },
  mounted() {
    this.$nextTick(() => {
      for (const key of ["original", "dubbed"]) {
        if (!this[key]) continue;
        const root = getHtmlElement(this[key]);
        const video = root?.matches("video") ? root : root?.querySelector("video");
        if (!video) continue;
        const sync = () => { if (this.active === key) this.sync(); };
        const play = () => this.select(key);
        const events = ["loadedmetadata", "durationchange", "timeupdate", "seeked", "emptied"];
        for (const event of events) video.addEventListener(event, sync);
        video.addEventListener("play", play);
        this.media[key] = {video, sync, play, events};
      }
      this.sync();
    });
  },
  beforeUnmount() {
    for (const {video, sync, play, events} of Object.values(this.media)) {
      for (const event of events) video.removeEventListener(event, sync);
      video.removeEventListener("play", play);
    }
  },
  methods: {
    select(key) {
      for (const [other, entry] of Object.entries(this.media)) {
        if (key !== other && !entry.video.paused) entry.video.pause();
      }
      this.active = key;
      this.sync();
    },
    sync() {
      const video = this.media[this.active]?.video;
      this.duration = Number.isFinite(video?.duration) ? video.duration : 0;
      this.position = this.duration ? Math.min(video.currentTime, this.duration) : 0;
    },
    seek(seconds) {
      const video = this.media[this.active]?.video;
      if (!video || !this.duration) return;
      video.currentTime = Math.max(0, Math.min(seconds, this.duration));
      this.sync();
    },
    seekFromTrack(event) {
      const {left, width} = event.currentTarget.getBoundingClientRect();
      this.seek((event.clientX - left) / width * this.duration);
    },
    beginRange(which, event) {
      if (!this.duration) return;
      const seconds = this.rangePosition(event);
      this.draftRangeStart = this.rangeStart == null ? 0 : this.rangeStart;
      this.draftRangeEnd = this.rangeEnd == null ? this.duration : this.rangeEnd;
      if (which === "selection") {
        this.draftRangeStart = seconds;
        this.draftRangeEnd = seconds;
      }
      this.rangeDrag = {which, pointerId:event.pointerId};
      event.currentTarget.setPointerCapture?.(event.pointerId);
      if (which === "selection") this.updateSelection(seconds);
    },
    rangePosition(event) {
      const {left,width} = this.$refs.rangeTrack.getBoundingClientRect();
      return Math.max(0,Math.min(this.duration,(event.clientX-left)/width*this.duration));
    },
    updateSelection(seconds) {
      const minimum = Math.min(0.15,this.duration);
      if (this.rangeDrag.which === "selection") {
        this.draftRangeEnd = seconds;
        if (this.draftRangeEnd < this.draftRangeStart) {
          [this.draftRangeStart,this.draftRangeEnd] = [this.draftRangeEnd,this.draftRangeStart];
        }
      } else if (this.rangeDrag.which === "start") {
        this.draftRangeStart = Math.min(seconds,this.draftRangeEnd-minimum);
        this.draftRangeStart = Math.max(0,this.draftRangeStart);
      } else {
        this.draftRangeEnd = Math.max(seconds,this.draftRangeStart+minimum);
        this.draftRangeEnd = Math.min(this.duration,this.draftRangeEnd);
      }
    },
    moveRange(event) {
      if (!this.rangeDrag || event.pointerId !== this.rangeDrag.pointerId) return;
      this.updateSelection(this.rangePosition(event));
    },
    endRange(event) {
      if (!this.rangeDrag || event.pointerId !== this.rangeDrag.pointerId) return;
      const {draftRangeStart:start,draftRangeEnd:end} = this;
      this.rangeDrag = null;
      if (end-start >= Math.min(0.15,this.duration)) {
        this.$emit("range-change",{start:Number(start.toFixed(3)),end:Number(end.toFixed(3)),duration:this.duration});
      }
    },
    percent(seconds) {
      return `${Math.max(0, Math.min(100, seconds / this.duration * 100))}%`;
    },
    clock(seconds, decimal = true) {
      const ms = Math.max(0, Math.floor(seconds * 1000));
      const whole = Math.floor(ms / 1000);
      const minutes = Math.floor(whole / 60);
      const remainder = String(whole % 60).padStart(2, "0");
      return `${minutes}:${remainder}${decimal ? "." + Math.floor(ms % 1000 / 100) : ""}`;
    },
  },
};

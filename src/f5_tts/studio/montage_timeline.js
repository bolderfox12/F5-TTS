export default {
  props: {player:Number, clips:Array, selected:String, length:Number, labels:Object, tracks:Array},
  template: `
    <section :aria-label="labels.timeline" style="width:100%;background:#142b36;color:#e4f6f5;border-radius:14px;overflow:hidden;border:1px solid #25434d">
      <header style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;padding:12px 16px;border-bottom:1px solid #31505b">
        <div style="display:flex;align-items:center;gap:10px">
          <strong>{{ labels.timeline }}</strong><kbd style="color:#b1cdd1;font-size:10px">{{ labels.shortcut }}</kbd>
        </div>
        <div style="display:flex;align-items:center;gap:12px">
          <output style="font-variant-numeric:tabular-nums;color:#a8f0e6">{{ clock(position) }}</output>
          <label style="display:flex;align-items:center;gap:7px;font-size:12px">{{ labels.zoom }}
            <input type="range" v-model.number="zoom" min="1" max="8" step="0.25" style="width:92px;accent-color:#7bdccc">
          </label>
        </div>
      </header>
      <div style="display:flex;align-items:stretch">
        <div style="width:clamp(83px,13vw,145px);flex:none;background:#1a3541;border-right:1px solid #31505b">
          <div style="height:36px;display:flex;align-items:center;padding-left:10px;color:#bad3d8;font-size:11px">00:00</div>
          <div v-for="track in tracks" :key="track" @contextmenu.prevent="trackMenu(track,$event)" style="height:48px;border-top:1px solid #31505b;display:flex;align-items:center;gap:6px;padding:0 8px;font-size:11px">
            <strong style="color:#85d7cb">{{ track }}</strong><span>{{ labels[track] || labels.audioTrack }}</span>
          </div>
        </div>
        <div ref="scroll" style="overflow-x:auto;min-width:0;flex:1">
          <div ref="grid" style="min-width:100%;position:relative" :style="{width:gridWidth+'px'}">
            <div ref="ruler" role="slider" tabindex="0" :aria-label="labels.position" :aria-valuemin="0"
              :aria-valuemax="displayLength" :aria-valuenow="position" @pointerdown="seekOnGrid"
              @keydown.left.prevent="seek(position-0.1)" @keydown.right.prevent="seek(position+0.1)"
              style="position:relative;height:36px;cursor:ew-resize;color:#b8d4d8;font-size:10px">
              <span v-for="tick in ticks" :key="tick" :style="{left: percent(tick)}"
                style="position:absolute;bottom:0;border-left:1px solid #6a8790;padding:0 0 8px 4px;pointer-events:none">{{ clock(tick,false) }}</span>
            </div>
            <div v-for="track in tracks" :key="track" @pointerdown.self="seekOnGrid"
              :style="{backgroundSize:(tickStep/displayLength*100)+'% 100%'}"
              style="border-top:1px solid #31505b;height:48px;position:relative;background-image:linear-gradient(to right,#34505c 1px,transparent 1px)">
              <button type="button" v-for="clip in clips.filter(row=>row.track===track)" :key="clip.id"
                :title="clip.name+' · '+clock(clip.start)" :aria-pressed="clip.id===selected"
                :style="clipStyle(clip)" @pointerdown.stop="beginDrag(clip,$event)"
                @pointermove="dragTo($event)" @pointerup="endDrag($event)" @pointercancel="drag=null"
                @contextmenu.prevent.stop="clipMenu(clip,$event)"
                @keydown.enter.prevent="select(clip)" @keydown.space.prevent="select(clip)"
                @keydown.left.prevent="nudge(clip,-0.1)" @keydown.right.prevent="nudge(clip,0.1)"
                style="position:absolute;top:5px;height:37px;min-width:14px;border-radius:7px;text-align:left;
                  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:0 8px;font-weight:650;font-size:11px;
                  cursor:grab;touch-action:none;color:#122b33">
                <span v-if="clip.fx" style="margin-right:4px;border-radius:3px;background:#233f47;color:#fff;padding:2px 4px">{{ labels.fx }}</span>{{ clip.name }}
              </button>
            </div>
            <div :style="{left:percent(position)}" aria-hidden="true" style="position:absolute;top:0;bottom:0;width:2px;
              background:#ecfff7;box-shadow:0 0 8px #63e2c9;pointer-events:none"></div>
          </div>
        </div>
      </div>
      <teleport to="body">
        <div v-if="menu" role="menu" :style="{left:menu.x+'px',top:menu.y+'px'}"
          style="position:fixed;z-index:7000;background:white;color:#143944;border:1px solid #a4c4c4;border-radius:8px;box-shadow:0 6px 20px #132f4250;padding:4px">
          <button type="button" role="menuitem" @click="duplicateMenu" style="padding:8px 12px;width:100%;text-align:left">
            {{ menu.payload.track ? labels.duplicateTrack : labels.duplicate }}
          </button>
          <button v-if="menu.hasAudio" type="button" role="menuitem" @click="audioEffectsMenu"
            style="padding:8px 12px;width:100%;text-align:left">{{ labels.audioEffects }}</button>
          <button v-if="menu.hasVideo" type="button" role="menuitem" @click="videoEffectsMenu"
            style="padding:8px 12px;width:100%;text-align:left">{{ labels.videoEffects }}</button>
        </div>
      </teleport>
    </section>`,
  data() {
    return {position:0, zoom:1, drag:null, menu:null, media:null,
      colors:{V1:"#7be0cc",V2:"#a1c8f1",A1:"#a7e39d",A2:"#d6b9ef",CC:"#f0c977",TX:"#edbfa7"}};
  },
  computed:{
    displayLength(){return Math.max(this.length,1);},
    gridWidth(){return Math.min(120000, Math.max(690, this.displayLength * 24) * this.zoom);},
    tickStep(){
      const target=this.displayLength*74/this.gridWidth;
      const power=10**Math.floor(Math.log10(Math.max(target,1)));
      return [1,2,5,10].map(value=>value*power).find(value=>value>=target) || 1;
    },
    ticks(){return Array.from({length:Math.ceil(this.displayLength/this.tickStep)},(_,index)=>index*this.tickStep);},
  },
  mounted(){
    document.addEventListener("keydown",this.handleKey);
    document.addEventListener("pointerdown",this.dismissMenu);
    this.$nextTick(()=>{
      const root=getHtmlElement(this.player);
      this.media=root?.matches("video")?root:root?.querySelector("video");
      this.media?.addEventListener("timeupdate",this.updatePosition);
      this.media?.addEventListener("seeked",this.reflectSeek);
      this.media?.addEventListener("pause",this.reflectSeek);
    });
  },
  beforeUnmount(){
    document.removeEventListener("keydown",this.handleKey);
    document.removeEventListener("pointerdown",this.dismissMenu);
    this.media?.removeEventListener("timeupdate",this.updatePosition);
    this.media?.removeEventListener("seeked",this.reflectSeek);
    this.media?.removeEventListener("pause",this.reflectSeek);
  },
  methods:{
    clock(time, milliseconds=true){
      const total=Math.round(Math.max(0,time)*1000);
      const minutes=String(Math.floor(total/60000)).padStart(2,"0");
      const seconds=String(Math.floor(total/1000)%60).padStart(2,"0");
      return minutes+":"+seconds+(milliseconds?"."+String(total%1000).padStart(3,"0"):"");
    },
    percent(seconds){return 100*Math.min(this.displayLength,Math.max(0,seconds))/this.displayLength+"%";},
    handleKey(event){
      if (event.key === "Escape" && this.menu) { this.menu=null; return; }
      if (event.code !== "KeyS" || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      if (event.target?.closest?.("input,textarea,select,[contenteditable],[role=dialog]")) return;
      event.preventDefault();
      this.menu=null;
      this.$emit("split",{id:this.selected,position:Number(this.position.toFixed(3))});
    },
    openMenu(payload,event,hasAudio=false,hasVideo=false){
      this.menu={payload,hasAudio,hasVideo,x:Math.max(4,Math.min(event.clientX,window.innerWidth-170)),
        y:Math.max(4,Math.min(event.clientY,window.innerHeight-(hasAudio||hasVideo?110:54)))};
    },
    clipMenu(clip,event){this.$emit("select-clip",clip.id);this.openMenu({id:clip.id},event,clip.has_audio,clip.track==="V1");},
    trackMenu(track,event){if(["V1","V2"].includes(track)||/^A[1-9][0-9]*$/.test(track))this.openMenu({track},event);},
    duplicateMenu(){if(this.menu)this.$emit("duplicate",this.menu.payload);this.menu=null;},
    audioEffectsMenu(){if(this.menu?.hasAudio)this.$emit("audio-effects",this.menu.payload.id);this.menu=null;},
    videoEffectsMenu(){if(this.menu?.hasVideo)this.$emit("video-effects",this.menu.payload.id);this.menu=null;},
    dismissMenu(event){if(!event.target?.closest?.("[role=menu]"))this.menu=null;},
    clipStyle(clip){
      const start=this.drag?.id===clip.id?this.drag.position:clip.start;
      return {left:this.percent(start),width:this.percent(clip.out-clip.in),background:this.colors[clip.track] || "#aad9ce",
        border:clip.id===this.selected?"2px solid #fff":"1px solid #28404a"};
    },
    updatePosition(){if(this.media && !this.media.paused) this.position=this.media.currentTime;},
    reflectSeek(){
      if(!this.media || !Number.isFinite(this.media.duration))return;
      if(this.position>this.media.duration && this.media.currentTime>=this.media.duration-0.03)return;
      this.position=this.media.currentTime;
      this.$emit("seek",Number(this.position.toFixed(3)));
    },
    seek(time){
      this.position=Math.min(this.displayLength,Math.max(0,time));
      if (this.media && Number.isFinite(this.media.duration)) this.media.currentTime=Math.min(this.position,this.media.duration);
      this.$emit("seek",Number(this.position.toFixed(3)));
    },
    seekOnGrid(event){
      const box=this.$refs.grid.getBoundingClientRect();
      this.seek((event.clientX-box.left)/box.width*this.displayLength);
    },
    select(clip){this.$emit("select-clip",clip.id);this.seek(clip.start);},
    beginDrag(clip,event){
      if(event.button!==0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      this.drag={id:clip.id,origin:event.clientX,start:clip.start,position:clip.start,changed:false};
      this.$emit("select-clip",clip.id);
    },
    dragTo(event){
      if(!this.drag || Math.abs(event.clientX-this.drag.origin)<3 && !this.drag.changed) return;
      const delta=(event.clientX-this.drag.origin)/this.$refs.grid.getBoundingClientRect().width*this.displayLength;
      this.drag.position=Math.max(0,Math.round((this.drag.start+delta)*20)/20);
      this.drag.changed=true;
    },
    endDrag(){
      if(!this.drag)return;
      const drag=this.drag;
      this.drag=null;
      if(drag.changed)this.$emit("move-clip",{id:drag.id,start:drag.position});
      this.seek(drag.position);
    },
    nudge(clip,delta){
      const start=Math.max(0,Math.round((clip.start+delta)*1000)/1000);
      this.$emit("move-clip",{id:clip.id,start});
      this.seek(start);
    },
  },
};

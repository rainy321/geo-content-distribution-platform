<template>
  <div ref="frameRef" class="visibility-trend" :class="{ empty: normalizedValues.length === 0 }">
    <canvas
      ref="canvasRef"
      role="img"
      :aria-label="accessibleLabel"
    />
    <p v-if="normalizedValues.length === 0">完成至少一次 AI 可见度分析后，这里会出现真实趋势。</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  values: { type: Array, default: () => [] },
  labels: { type: Array, default: () => [] },
  metricLabel: { type: String, default: '品牌可见度' }
})

const canvasRef = ref(null)
const frameRef = ref(null)
let resizeObserver = null

const normalizedValues = computed(() => props.values
  .map(value => Number(value))
  .filter(value => Number.isFinite(value))
  .map(value => Math.max(0, Math.min(100, value))))

const accessibleLabel = computed(() => {
  if (!normalizedValues.value.length) return `${props.metricLabel}暂无历史数据`
  const first = normalizedValues.value[0]
  const latest = normalizedValues.value.at(-1)
  return `${props.metricLabel}共 ${normalizedValues.value.length} 个记录，从 ${first.toFixed(1)} 到 ${latest.toFixed(1)}`
})

const draw = () => {
  const canvas = canvasRef.value
  const frame = frameRef.value
  if (!canvas || !frame) return

  const width = Math.max(320, Math.floor(frame.clientWidth))
  const height = Math.max(170, Math.floor(frame.clientHeight))
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  canvas.width = Math.floor(width * ratio)
  canvas.height = Math.floor(height * ratio)
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`

  const context = canvas.getContext('2d')
  if (!context) return
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)

  const plot = { left: 36, right: width - 12, top: 12, bottom: height - 26 }
  const plotHeight = plot.bottom - plot.top
  context.font = '10px "Cascadia Mono", Consolas, monospace'
  context.textBaseline = 'middle'

  for (const tick of [100, 75, 50, 25, 0]) {
    const y = plot.top + ((100 - tick) / 100) * plotHeight
    context.beginPath()
    context.moveTo(plot.left, y)
    context.lineTo(plot.right, y)
    context.strokeStyle = '#e7e7e3'
    context.lineWidth = 1
    context.stroke()
    context.fillStyle = '#8a8c91'
    context.fillText(String(tick), 2, y)
  }

  const values = normalizedValues.value
  if (!values.length) return
  const denominator = Math.max(1, values.length - 1)
  const points = values.map((value, index) => ({
    x: plot.left + (index / denominator) * (plot.right - plot.left),
    y: plot.top + ((100 - value) / 100) * plotHeight
  }))

  context.beginPath()
  context.moveTo(points[0].x, plot.bottom)
  points.forEach(point => context.lineTo(point.x, point.y))
  context.lineTo(points.at(-1).x, plot.bottom)
  context.closePath()
  context.fillStyle = 'rgba(17, 18, 20, 0.045)'
  context.fill()

  context.beginPath()
  points.forEach((point, index) => {
    if (index === 0) context.moveTo(point.x, point.y)
    else context.lineTo(point.x, point.y)
  })
  context.strokeStyle = '#111214'
  context.lineWidth = 2
  context.lineJoin = 'round'
  context.lineCap = 'round'
  context.stroke()

  const latest = points.at(-1)
  context.beginPath()
  context.arc(latest.x, latest.y, 4, 0, Math.PI * 2)
  context.fillStyle = '#ffffff'
  context.fill()
  context.strokeStyle = '#111214'
  context.lineWidth = 2
  context.stroke()

  const labelIndexes = [...new Set([0, Math.floor((values.length - 1) / 2), values.length - 1])]
  context.fillStyle = '#8a8c91'
  context.textBaseline = 'bottom'
  labelIndexes.forEach(index => {
    const point = points[index]
    const label = String(props.labels[index] || `#${index + 1}`)
    context.textAlign = index === 0 ? 'left' : index === values.length - 1 ? 'right' : 'center'
    context.fillText(label, point.x, height - 2)
  })
}

watch(() => [props.values, props.labels], () => nextTick(draw), { deep: true })

onMounted(() => {
  resizeObserver = new ResizeObserver(draw)
  if (frameRef.value) resizeObserver.observe(frameRef.value)
  nextTick(draw)
})

onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<style scoped>
.visibility-trend {
  position: relative;
  width: 100%;
  min-height: 190px;
}

.visibility-trend canvas {
  display: block;
  width: 100%;
  height: 190px;
}

.visibility-trend.empty {
  display: grid;
  place-items: center;
}

.visibility-trend.empty canvas {
  grid-area: 1 / 1;
}

.visibility-trend.empty p {
  z-index: 1;
  grid-area: 1 / 1;
  max-width: 280px;
  margin: 0;
  color: #77797d;
  font-size: 12px;
  line-height: 1.7;
  text-align: center;
}
</style>

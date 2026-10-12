(function(root) {
  'use strict';
  class Cloud {
    constructor(a, b) {
      this.a = a; this.b = b; this.polygons = [];
      this.view = {zOrder: () => 'bottom', renderer: () => ({draw: target => this.draw(target)})};
    }
    attached({chart}) { this.chart = chart; }
    detached() { this.chart = null; this.polygons = []; }
    paneViews() { return [this.view]; }
    updateAllViews() {
      this.polygons = [];
      if (!this.chart || !this.a.options().visible || !this.b.options().visible) return;
      const a = this.a.data(), b = new Map(this.b.data().map(point => [point.time, point.value]));
      const range = this.chart.timeScale().getVisibleRange();
      if (!range) return;
      // Library data() omits whitespace; join the differently warmed-up spans by time.
      for (let i = 1; i < a.length; i++) {
        if (a[i].time < range.from) continue;
        if (a[i-1].time > range.to) break;
        const values = [a[i-1].value, b.get(a[i-1].time), a[i].value, b.get(a[i].time)];
        if (!values.every(Number.isFinite)) continue;
        const x0 = this.chart.timeScale().timeToCoordinate(a[i-1].time), x1 = this.chart.timeScale().timeToCoordinate(a[i].time);
        const y = values.map(value => this.a.priceToCoordinate(value));
        if (x0 === null || x1 === null || y.some(value => value === null)) continue;
        const d0 = values[0] - values[1], d1 = values[2] - values[3];
        if (d0 * d1 < 0) {
          const t = d0 / (d0 - d1), x = x0 + t * (x1 - x0), cross = y[0] + t * (y[2] - y[0]);
          this.polygons.push({points: [[x0,y[0]], [x,cross], [x0,y[1]]], up: d0 > 0});
          this.polygons.push({points: [[x,cross], [x1,y[2]], [x1,y[3]]], up: d1 > 0});
        } else this.polygons.push({points: [[x0,y[0]], [x1,y[2]], [x1,y[3]], [x0,y[1]]], up: d1 >= 0});
      }
    }
    draw(target) {
      target.useMediaCoordinateSpace(({context}) => {
        for (const polygon of this.polygons) {
          context.fillStyle = polygon.up ? 'rgba(18,184,134,0.16)' : 'rgba(255,92,122,0.16)';
          context.beginPath(); polygon.points.forEach(([x,y], i) => i ? context.lineTo(x,y) : context.moveTo(x,y));
          context.closePath(); context.fill();
        }
      });
    }
  }
  function attach(configs, series) {
    const groups = new Map();
    configs.forEach((config, index) => {
      if (!config.cloud) return;
      const group = groups.get(config.indicatorId) || {};
      group[config.cloud] = series[index]; groups.set(config.indicatorId, group);
    });
    for (const {a,b} of groups.values()) if (a && b) a.attachPrimitive(new Cloud(a,b));
  }
  root.ChartStudyClouds = {attach};
})(window);

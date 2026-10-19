/* Decorative packets represent measured throughput, never real network packets. */
globalThis.MediaSeedPackets = (() => {
  const sizes = ['small', 'medium', 'large'];
  const durations = [8.4, 5.6, 3.2];
  function count(rate) {
    return typeof rate === 'number' && Number.isFinite(rate) && rate > 0
      ? Math.min(32, Math.max(1, Math.ceil(2 * Math.log2(1 + rate / 16384)))) : 0;
  }
  function mount(route) {
    const overlay = document.createElement('span');
    overlay.className = 'route-packets'; overlay.setAttribute('aria-hidden', 'true');
    const lanes = ['download', 'upload'].map(direction => {
      const lane = document.createElement('span');
      lane.className = 'packet-lane packet-lane-' + direction;
      overlay.append(lane); return {lane, target: 0, serial: 0};
    });
    route.append(overlay);
    function resize() {
      const icons = route.querySelectorAll('.route-icon');
      if (icons.length < 2) return;
      const bounds = route.getBoundingClientRect();
      const first = icons[0].getBoundingClientRect(), last = icons[icons.length - 1].getBoundingClientRect();
      if (!bounds.width || !first.width || !last.width) return;
      const start = first.left + first.width / 2 - bounds.left;
      const end = last.left + last.width / 2 - bounds.left;
      overlay.style.left = start + 'px'; overlay.style.width = (end - start) + 'px';
      overlay.style.top = (first.top + first.height / 2 - bounds.top) + 'px';
      overlay.style.setProperty('--packet-distance', (end - start + 20) + 'px');
    }
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(resize).observe(route);
    function updateLane(state, target, clear) {
      state.target = target;
      if (clear) {state.lane.replaceChildren(); return;}
      const reduced = document.documentElement.dataset.reducedMotion === 'true'
        || globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      if (reduced) while (state.lane.children.length > target) state.lane.lastElementChild.remove();
      while (state.lane.children.length < target) {
        const serial = state.serial++, size = serial % sizes.length;
        const packet = document.createElement('i'); packet.className = 'traffic-packet packet-' + sizes[size];
        packet.style.setProperty('--packet-duration', durations[size] + 's');
        const phase = (serial * .61803398875) % 1;
        packet.style.animationDelay = (phase * durations[size]) + 's';
        packet.style.setProperty('--packet-rest', (phase * 100) + '%');
        packet.addEventListener('animationiteration', () => {
          if (state.lane.children.length > state.target) packet.remove();
        });
        state.lane.append(packet);
      }
    }
    return {update(down, up, verified) {
      const measured = verified && [down, up].every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0);
      updateLane(lanes[0], measured ? count(down) : 0, !measured);
      updateLane(lanes[1], measured ? count(up) : 0, !measured);
      resize();
    }};
  }
  return {count, mount};
})();

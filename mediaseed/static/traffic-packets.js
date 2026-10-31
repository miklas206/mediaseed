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
    // Share the route animation clock: boarding happens at the measured VPN hop,
    // including reverse upload traffic. CSSOM keeps this compatible with the CSP.
    let boardingRules = null, boardingPosition = '';
    function placeBoarding(first, last, vpn) {
      if (!vpn || last.left <= first.left) return;
      const position = Math.max(5, Math.min(95, 100 *
        (vpn.left + vpn.width / 2 - first.left - first.width / 2 + 20) /
        (last.left + last.width / 2 - first.left - first.width / 2 + 40)));
      const key = position.toFixed(2);
      if (key === boardingPosition) return;
      if (!boardingRules) {
        const names = ['river-duck-board-down', 'river-duck-board-up', 'river-leaf-board-down', 'river-leaf-board-up'];
        const rules = [];
        for (const sheet of document.styleSheets) {
          try { rules.push(...sheet.cssRules); } catch { /* Cross-origin styles are unrelated. */ }
        }
        boardingRules = names.map(name => rules.find(rule => rule.name === name && rule.appendRule));
        if (boardingRules.some(rule => !rule)) { boardingRules = null; return; }
      }
      const before = (position - 2).toFixed(2), after = (position + 2).toFixed(2);
      const swim = 'transform:translateY(0) rotate(0deg)', ride = 'transform:translateY(-5px) rotate(0deg)', hop = 'transform:translateY(-12px) rotate(-8deg)';
      const frames = [
        [['0',swim],[before,swim],[key,hop],[after,ride],['100',ride]],
        [['0',ride],[before,ride],[key,hop],[after,swim],['100',swim]],
        [['0','opacity:0;transform:scale(.4)'],[before,'opacity:0;transform:scale(.4)'],[after,'opacity:1;transform:scale(1)'],['100','opacity:1;transform:scale(1)']],
        [['0','opacity:1;transform:scale(1)'],[before,'opacity:1;transform:scale(1)'],[after,'opacity:0;transform:scale(.4)'],['100','opacity:0;transform:scale(.4)']],
      ];
      boardingRules.forEach((rule, index) => {
        for (const frame of [...rule.cssRules]) rule.deleteRule(frame.keyText);
        for (const [offset, value] of frames[index]) rule.appendRule(offset + '%{' + value + '}');
      });
      boardingPosition = key;
    }
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
      placeBoarding(first, last, route.querySelector('.route-vpn .route-icon')?.getBoundingClientRect());
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
      updateLane(lanes[0], measured ? count(down) : 0, !measured || down === 0);
      updateLane(lanes[1], measured ? count(up) : 0, !measured || up === 0);
      resize();
    }};
  }
  return {count, mount};
})();

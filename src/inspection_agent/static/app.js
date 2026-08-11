document.addEventListener("DOMContentLoaded", () => {
  const upload = document.querySelector("#image-upload");
  const preview = document.querySelector("#image-preview");
  if (upload && preview) {
    upload.addEventListener("change", () => {
      const file = upload.files?.[0];
      if (!file) return;
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
    });
  }

  const scenario = document.querySelector("#scenario-select");
  const dataset = document.querySelector("#dataset-id");
  if (scenario && dataset) {
    const updateDataset = () => {
      dataset.value = scenario.selectedOptions[0]?.dataset.dataset || "";
    };
    scenario.addEventListener("change", updateDataset);
    updateDataset();
  }

  const chartNodes = document.querySelectorAll(".sensor-chart[data-config]");
  if (chartNodes.length && window.Plotly) {
    const series = [...chartNodes].map(node => JSON.parse(node.dataset.config || "{}"));
    const colors = ["#27e0a3", "#ffb84d", "#5aa8ff"];
    series.forEach((item, index) => {
      const node = chartNodes[index];
      if (!node) return;
      const shapes = [
        {type: "line", x0: item.timestamps[0], x1: item.timestamps.at(-1), y0: item.operating_max, y1: item.operating_max, line: {color: "#ff6076", width: 1, dash: "dot"}},
        ...item.anomaly_segments.map(segment => ({type: "rect", x0: segment.start_time, x1: segment.end_time, y0: 0, y1: 1, yref: "paper", fillcolor: "rgba(255, 79, 110, .18)", line: {width: 0}}))
      ];
      Plotly.newPlot(node, [{x: item.timestamps, y: item.values, type: "scatter", mode: "lines", line: {color: colors[index % colors.length], width: 2}, hovertemplate: `%{x}<br>%{y:.3f} ${item.unit}<extra>${item.display_name}</extra>`}], {paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: {color: "#8fa5b5", family: "Inter, system-ui"}, margin: {l: 48, r: 18, t: 12, b: 38}, height: 220, xaxis: {gridcolor: "rgba(125, 152, 167, .12)"}, yaxis: {gridcolor: "rgba(125, 152, 167, .12)", title: item.unit}, shapes, showlegend: false}, {responsive: true, displayModeBar: false});
    });
  }
});

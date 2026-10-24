from(bucket: "${bucket}")
  |> range(start: -${lookback})
  |> filter(fn: (r) => r._measurement == "prediction")
  |> filter(fn: (r) => contains(value: r._field, set: ["failure_probability", "cycle", "latency_ms", "predicted_class"]))
  // Risk tags create separate series; merge them before choosing the latest field.
  |> group(columns: ["engine_id", "_field"])
  |> sort(columns: ["_time"])
  |> last()

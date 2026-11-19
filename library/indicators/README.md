# QuantDinger Indicators

This folder contains QuantDinger-compatible Python indicators in a flat, copy-friendly layout.

## Usage

1. Open an indicator `.py` file and copy its complete source.
2. Create an indicator in the QuantDinger indicator editor and paste the source.
3. Review and adjust the declared parameters.
4. Compile the indicator and preview it on a candlestick chart.

These files target the QuantDinger indicator runtime and normally use the platform-provided `df`, `params`, and chart-output contract. They are not intended to run as standalone Python programs.

## Validation notes

- Files should pass the applicable code and runtime validation before they are added.
- Each indicator should be visually reviewed in the QuantDinger chart environment.
- Behavior may vary across instruments, timeframes, data providers, and parameter values.
- Do not add validation caches, temporary scripts, proprietary data dependencies, or unsupported third-party libraries.

## Contributing

Contributions are welcome. Add a uniquely named `.py` file to this folder and include chart-preview evidence in the pull request. Follow the shared [library contribution requirements](../README.md#contributing).

Indicator output is for research and visualization. It is not investment advice.

# QuantDinger Library

The QuantDinger Library is a community-maintained collection of ready-to-copy Python indicators and Strategy API V2 strategies.

- [Browse indicators](./indicators/)
- [Browse strategies](./strategies/)

Both folders intentionally use a flat layout so files remain easy to browse, search, link, and copy from GitHub. Existing numeric prefixes are display labels only; they are not runtime identifiers and are not required for future contributions.

## How to use the library

1. Open the `.py` file you want to use and copy its complete source.
2. Create an indicator or strategy in the corresponding QuantDinger editor.
3. Paste the source and review its parameters, instrument, product, and timeframe.
4. Compile and preview indicators on a chart. Compile and backtest strategies before considering paper or live deployment.

This directory is a source library. It is not imported into the database or marketplace automatically.

## Validation scope

The files in this library passed the QuantDinger validation flow available at the time they were added. Market data, parameters, product type, timeframe, fees, slippage, and future engine changes can all affect runtime behavior and results. Revalidate a file in your own environment before using it for trading.

## Contributing

Community contributions are welcome. Add a self-contained `.py` file to [`indicators/`](./indicators/) or [`strategies/`](./strategies/) and open a pull request.

Please:

- use an English filename, docstring, parameter description, and user-facing label;
- choose a unique, descriptive filename instead of depending on a fixed sequence number;
- keep the file compatible with the current QuantDinger indicator or Strategy API V2 contract;
- include no credentials, private endpoints, local paths, generated caches, or validation artifacts;
- describe how the indicator was previewed or how the strategy was backtested;
- submit only work that you have the right to contribute under the repository license.

See the repository [contribution guide](../CONTRIBUTING.md) for the pull-request and DCO requirements.

## License

Unless a file states otherwise, content in this directory follows the repository's Apache-2.0 license.

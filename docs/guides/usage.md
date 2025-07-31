# Usage

Sonolus.py is mainly used via its command line interface (CLI) with the `sonolus-py` command.

## Development Server

To start a development server to test an engine, use the `dev` command:

```bash
sonolus-py dev
```

For larger projects, you might want to specify a specific mode to run to speed up startup:

```bash
sonolus-py dev --[play|watch|preview|tutorial]
```

## Checking for Errors

To just check for errors in the project without attempting to build it or starting a server, use the `check` command:

```bash
sonolus-py check
```

## Building for Production

To build engine data for production, use the `build` command:

```bash
sonolus-py build
```

This will create a `build/` directory containing the built engine data. These contents can be used in a 
production Sonolus server such a server written with 
[Sonolus Express](https://github.com/Sonolus/sonolus-express/tree/main).

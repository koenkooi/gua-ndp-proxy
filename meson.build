project(
  'gua-ndp-proxy',
  version: '0.1.0',
  license: 'MIT',
  meson_version: '>=0.61.0',
)

prefix = get_option('prefix')
bindir = prefix / get_option('bindir')
sysconfdir = prefix / get_option('sysconfdir')
docdir = prefix / get_option('datadir') / 'doc' / meson.project_name()

# Ask pkg-config where the distribution keeps its systemd directories, re-based
# onto our prefix; fall back to the conventional location where no .pc file
# is installed.
systemd_dep = dependency('systemd', required: false)

unitdir = get_option('systemdsystemunitdir')
if unitdir == ''
  unitdir = systemd_dep.get_variable(
    pkgconfig: 'systemdsystemunitdir',
    pkgconfig_define: ['prefix', prefix],
    default_value: prefix / 'lib' / 'systemd' / 'system',
  )
endif

sysctldir = get_option('sysctldir')
if sysctldir == ''
  sysctldir = systemd_dep.get_variable(
    pkgconfig: 'sysctldir',
    pkgconfig_define: ['prefix', prefix],
    default_value: prefix / 'lib' / 'sysctl.d',
  )
endif

install_data(
  'src/gua_ndp_proxy.py',
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
  rename: 'gua-ndp-proxy',
)

conf = configuration_data()
conf.set('bindir', bindir)
conf.set('sysconfdir', sysconfdir)
configure_file(
  input: 'data/gua-ndp-proxy.service.in',
  output: 'gua-ndp-proxy.service',
  configuration: conf,
  install_dir: unitdir,
)

if get_option('sysctl')
  install_data('data/60-gua-ndp-proxy.conf', install_dir: sysctldir)
endif

install_data(
  'LICENSE',
  'docs/design.md',
  'docs/configuration.md',
  'docs/installation.md',
  'docs/troubleshooting.md',
  install_dir: docdir,
)
install_data(
  'examples/gua-ndp-proxy.conf',
  'examples/sysctl-uplink-accept-ra.conf',
  'examples/downstream-link.network',
  install_dir: docdir / 'examples',
)

python = find_program('python3', version: '>=3.9')
sh = find_program('sh')
test_env = environment()
test_env.prepend('PYTHONPATH', meson.project_source_root() / 'src')

test(
  'unit',
  python,
  args: ['-m', 'unittest', 'discover', '-s', meson.project_source_root() / 'tests'],
  env: test_env,
)
test(
  'netns-integration',
  sh,
  args: [files('tests/netns_integration.sh')],
  env: test_env,
  timeout: 120,
)

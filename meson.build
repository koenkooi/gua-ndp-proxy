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
  'README.md',
  'CHANGELOG.md',
  'SECURITY.md',
  'docs/design.md',
  'docs/configuration.md',
  'docs/installation.md',
  'docs/troubleshooting.md',
  'docs/usb-gadget-subnet.md',
  install_dir: docdir,
)
install_data(
  'examples/gua-ndp-proxy.conf',
  'examples/sysctl-uplink-accept-ra.conf',
  'examples/downstream-link.network',
  install_dir: docdir / 'examples',
)

if get_option('gadget-subnet')
  libexecdir = prefix / get_option('libexecdir')
  udevrulesdir = get_option('udevrulesdir')
  if udevrulesdir == ''
    udev_dep = dependency('udev', required: false)
    # udev.pc's udevdir is absolute and not prefix-relative: udev only reads
    # rules from its own directories, so a custom prefix must not move them.
    udevrulesdir = udev_dep.get_variable(
      pkgconfig: 'udevdir',
      default_value: prefix / 'lib' / 'udev',
    ) / 'rules.d'
  endif

  install_data(
    'contrib/usb-gadget-subnet/usb-gadget-subnet.sh',
    install_dir: libexecdir / meson.project_name(),
    install_mode: 'rwxr-xr-x',
    rename: 'usb-gadget-subnet',
  )
  rules_conf = configuration_data()
  rules_conf.set('libexecdir', libexecdir)
  configure_file(
    input: 'contrib/usb-gadget-subnet/99-gua-usb-gadget-subnet.rules.in',
    output: '99-gua-usb-gadget-subnet.rules',
    configuration: rules_conf,
    install_dir: udevrulesdir,
  )
  install_data(
    'contrib/usb-gadget-subnet/gadget-subnet.conf.example',
    install_dir: docdir / 'examples',
  )
endif

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
test(
  'gadget-subnet',
  find_program('bash'),
  args: [files('tests/gadget_subnet_test.sh')],
  timeout: 60,
)

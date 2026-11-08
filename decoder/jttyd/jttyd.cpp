// jttyd - JTTY streaming receiver for FT-950 Remote.
//
// The first receive step sets up the decoder's tables (20-35 s on a Pi 5);
// {"ready":true} is printed after it.
//
// Feeds 12 kHz mono 16-bit audio to the streaming JTTY receiver of WSJT-X 3.3
// (jtty_rx_* in libwsjt_fort, GPL v3, K1JT/K9AN/G4KLA and the WSJT-X team)
// and prints every receive update as one JSON line on stdout:
//   {"id":..,"f":1500.2,"text":"CQ DJ0SH JO62","start":3.21,"latest":5.10,"term":0,"snr":-7}
// term: 0 growing, 1 complete, 2 expired, 3 reception ended.
//
// Input: WAV files given as arguments (decoded one after the other as one
// stream), or raw s16le 12 kHz on stdin when no file is given ("-" = stdin).
// Timing figures go to stderr at the end (--stats) - the cost of one receive
// step decides whether live decoding keeps up (a step covers 0.472 s at 384
// samples/symbol).
//
// Build: see build.sh (links libwsjt_fort.a of the WSJT-X 3.3 build).

#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" {
std::int32_t jtty_rx_create();
void jtty_rx_destroy(std::int32_t handle);
void jtty_rx_begin(std::int32_t handle, std::int64_t sessionId, std::int64_t gridOrigin,
                   std::int64_t firstSearch, std::int32_t samplesPerSymbol);
std::int32_t jtty_rx_process(std::int32_t handle, std::int16_t const* samples, std::int32_t count,
                             std::int64_t firstSample, std::int64_t stopSample, std::int32_t maxSteps,
                             std::int32_t lowFrequency, std::int32_t highFrequency,
                             float centerFrequency, float tolerance);
std::int64_t jtty_rx_next_required_sample(std::int32_t handle);
std::int64_t jtty_rx_next_search_sample(std::int32_t handle);
std::int32_t jtty_rx_take_updates(std::int32_t handle, std::int32_t capacity, char* text,
                                  std::int64_t* ids, float* frequencies, double* starts,
                                  double* latest, std::int32_t* terminals, std::int32_t* snrs);
void jtty_rx_end(std::int32_t handle, std::int32_t reason);
std::int32_t jtty_tx_encode(std::int32_t is_template, char const* text, std::int32_t text_length,
                            char const* his_call, std::int32_t his_call_length,
                            char const* exchange, std::int32_t exchange_length,
                            char const* my_call, std::int32_t my_call_length,
                            char const* grid, std::int32_t grid_length,
                            std::int32_t serial, std::int32_t serial_given,
                            std::int32_t report, std::int32_t report_given,
                            std::int32_t profile, std::int32_t is_final);
std::int32_t jtty_tx_status(std::int32_t handle, std::int32_t* segments, std::int32_t* substituted);
void jtty_tx_error(std::int32_t handle, char* key, std::int32_t key_capacity, std::int32_t* key_length,
                   char* detail, std::int32_t detail_capacity, std::int32_t* detail_length);
std::int32_t jtty_tx_segment(std::int32_t handle, std::int32_t index, std::int32_t* tones,
                             char* frames, std::int32_t* nframes, char* text, std::int32_t* text_length,
                             char* canonical, std::int32_t* canonical_length,
                             std::int32_t* is_final, std::int32_t* substituted);
void jtty_tx_destroy(std::int32_t handle);
std::int32_t jtty_tx_render(std::int32_t const* tones, std::int32_t nsym, std::int32_t sample_rate,
                            float f0, std::int16_t* samples, std::int32_t capacity);
}

namespace {

struct Options {
  int nsps = 384, low = 200, high = 2800;
  float center = 1500.0f, tol = 50.0f;
  bool stats = false;
  bool tx = false;                          // --tx TEXT: transmit audio instead of receiving
  std::string tx_text, my_call;
  int rate = 48000, profile = 0;            // profile 0 none, 1 field day, 2 RTTY roundup
  float f0 = 1500.0f;
  std::vector<std::string> files;
};

std::string json_escape(char const* s, int n) {
  std::string out;
  for (int i = 0; i < n && s[i]; ++i) {
    unsigned char c = static_cast<unsigned char>(s[i]);
    if (c == '"' || c == '\\') { out += '\\'; out += char(c); }
    else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); out += b; }
    else out += char(c);
  }
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

void emit_updates(std::int32_t h) {
  constexpr int cap = 30;
  static char text[cap * 80];
  static std::int64_t ids[cap];
  static float freqs[cap];
  static double starts[cap], latest[cap];
  static std::int32_t terms[cap], snrs[cap];
  for (;;) {
    int n = jtty_rx_take_updates(h, cap, text, ids, freqs, starts, latest, terms, snrs);
    for (int i = 0; i < n; ++i) {
      std::printf("{\"id\":%lld,\"f\":%.1f,\"text\":\"%s\",\"start\":%.3f,\"latest\":%.3f,"
                  "\"term\":%d,\"snr\":%d}\n",
                  static_cast<long long>(ids[i]), freqs[i], json_escape(text + i * 80, 80).c_str(),
                  starts[i], latest[i], terms[i], snrs[i]);
    }
    if (n < cap) break;
  }
  std::fflush(stdout);
}

// a minimal WAV reader: 16-bit PCM mono, 12000 Hz (what the digi slices and sjtty write)
bool read_wav(std::string const& path, std::vector<std::int16_t>& out) {
  FILE* f = std::fopen(path.c_str(), "rb");
  if (!f) { std::fprintf(stderr, "jttyd: cannot open %s\n", path.c_str()); return false; }
  char riff[12];
  if (std::fread(riff, 1, 12, f) != 12 || std::memcmp(riff, "RIFF", 4) || std::memcmp(riff + 8, "WAVE", 4)) {
    std::fprintf(stderr, "jttyd: %s is not a WAV file\n", path.c_str()); std::fclose(f); return false;
  }
  std::uint16_t channels = 0, bits = 0; std::uint32_t rate = 0;
  for (;;) {
    char id[4]; std::uint32_t size;
    if (std::fread(id, 1, 4, f) != 4 || std::fread(&size, 4, 1, f) != 1) break;
    if (!std::memcmp(id, "fmt ", 4)) {
      std::vector<unsigned char> b(size);
      if (std::fread(b.data(), 1, size, f) != size) break;
      std::memcpy(&channels, &b[2], 2); std::memcpy(&rate, &b[4], 4); std::memcpy(&bits, &b[14], 2);
    } else if (!std::memcmp(id, "data", 4)) {
      if (channels != 1 || bits != 16 || rate != 12000) {
        std::fprintf(stderr, "jttyd: %s: need 12000 Hz mono 16-bit (got %u Hz, %u ch, %u bit)\n",
                     path.c_str(), rate, channels, bits);
        std::fclose(f); return false;
      }
      std::size_t n0 = out.size(); out.resize(n0 + size / 2);
      std::size_t got = std::fread(out.data() + n0, 2, size / 2, f); out.resize(n0 + got);
      std::fclose(f); return true;
    } else {
      std::fseek(f, size + (size & 1), SEEK_CUR);
    }
  }
  std::fprintf(stderr, "jttyd: %s has no data chunk\n", path.c_str()); std::fclose(f); return false;
}

// --tx: the text as JTTY audio (all its segments one after the other), raw
// s16le mono at --rate on stdout; a JSON summary on stderr.
int transmit(Options const& o) {
  std::string const& t = o.tx_text;
  std::int32_t h = jtty_tx_encode(0, t.data(), std::int32_t(t.size()), "", 0, "", 0,
                                  o.my_call.data(), o.my_call.empty() ? -1 : std::int32_t(o.my_call.size()),
                                  "", -1, 0, 0, 0, 0, o.profile, 1);
  if (!h) { std::fprintf(stderr, "{\"ok\":false,\"error\":\"encoder unavailable\"}\n"); return 1; }
  std::int32_t segments = 0, substituted = 0;
  std::int32_t status = jtty_tx_status(h, &segments, &substituted);
  if (status != 0) {
    char key[64] = {0}, detail[256] = {0}; std::int32_t kl = 0, dl = 0;
    jtty_tx_error(h, key, sizeof key, &kl, detail, sizeof detail, &dl);
    std::fprintf(stderr, "{\"ok\":false,\"status\":%d,\"error\":\"%s\"}\n", status,
                 json_escape(detail, dl).c_str());
    jtty_tx_destroy(h);
    return 1;
  }
  std::vector<std::int32_t> tones(16 * 59);
  std::vector<char> frames(16 * 34);
  char text[96], canon[96];
  std::vector<std::int16_t> pcm;
  std::string sent;
  int nframes_total = 0;
  for (std::int32_t i = 0; i < segments; ++i) {
    std::int32_t nframes = 0, tl = 0, cl = 0, fin = 0, sub = 0;
    std::int32_t nsym = jtty_tx_segment(h, i, tones.data(), frames.data(), &nframes, text, &tl,
                                        canon, &cl, &fin, &sub);
    if (nsym <= 0) break;
    nframes_total += nframes;
    std::int32_t cap = nsym * (o.rate / 31) + o.rate;          // > nsym samples-per-symbol + ramps
    std::vector<std::int16_t> seg(cap);
    std::int32_t n = jtty_tx_render(tones.data(), nsym, o.rate, o.f0, seg.data(), cap);
    if (n <= 0) { std::fprintf(stderr, "{\"ok\":false,\"error\":\"render failed\"}\n"); jtty_tx_destroy(h); return 1; }
    pcm.insert(pcm.end(), seg.begin(), seg.begin() + n);
    if (!sent.empty()) sent += ' ';
    sent += json_escape(canon, cl);
  }
  jtty_tx_destroy(h);
  std::fwrite(pcm.data(), 2, pcm.size(), stdout);
  std::fflush(stdout);
  std::fprintf(stderr, "{\"ok\":true,\"segments\":%d,\"frames\":%d,\"seconds\":%.3f,"
               "\"substituted\":%d,\"text\":\"%s\"}\n", segments, nframes_total,
               double(pcm.size()) / o.rate, substituted, sent.c_str());
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  Options o;
  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto val = [&](char const* name) -> char const* {
      if (i + 1 >= argc) { std::fprintf(stderr, "jttyd: %s needs a value\n", name); std::exit(2); }
      return argv[++i];
    };
    if (a == "--nsps") o.nsps = std::atoi(val("--nsps"));
    else if (a == "--low") o.low = std::atoi(val("--low"));
    else if (a == "--high") o.high = std::atoi(val("--high"));
    else if (a == "--center") o.center = std::strtof(val("--center"), nullptr);
    else if (a == "--tol") o.tol = std::strtof(val("--tol"), nullptr);
    else if (a == "--stats") o.stats = true;
    else if (a == "--tx") { o.tx = true; o.tx_text = val("--tx"); }
    else if (a == "--f0") o.f0 = std::strtof(val("--f0"), nullptr);
    else if (a == "--rate") o.rate = std::atoi(val("--rate"));
    else if (a == "--my-call") o.my_call = val("--my-call");
    else if (a == "--profile") o.profile = std::atoi(val("--profile"));
    else if (a == "-h" || a == "--help") {
      std::printf("usage: jttyd [--nsps 240|320|384|480] [--low HZ] [--high HZ] [--center HZ] [--tol HZ]\n"
                  "             [--stats] [file.wav ... | -]\n"
                  "       jttyd --tx TEXT [--f0 HZ] [--rate HZ] [--my-call CALL] [--profile 0|1|2]\n"
                  "             (raw s16le mono on stdout, a JSON summary on stderr)\n");
      return 0;
    } else o.files.push_back(a);
  }
  if (o.nsps != 240 && o.nsps != 320 && o.nsps != 384 && o.nsps != 480) {
    std::fprintf(stderr, "jttyd: nsps must be 240, 320, 384 or 480\n"); return 2;
  }
  if (o.tx) return transmit(o);
  bool from_stdin = o.files.empty() || (o.files.size() == 1 && o.files[0] == "-");

  std::int32_t h = jtty_rx_create();
  if (!h) { std::fprintf(stderr, "jttyd: jtty_rx_create failed\n"); return 1; }
  jtty_rx_begin(h, 1, 0, 0, o.nsps);
  std::int64_t const nframe = 59LL * o.nsps, window = nframe + nframe / 4;

  std::vector<std::int16_t> hist;           // samples from `base` on
  std::int64_t base = 0;
  long steps = 0; double busy = 0, worst = 0, first_step = -1, worst_after = 0;
  auto t_start = std::chrono::steady_clock::now();

  auto pump = [&]() -> bool {               // run every step the buffered audio allows
    for (;;) {
      std::int64_t search = jtty_rx_next_search_sample(h);
      std::int64_t end = base + std::int64_t(hist.size());
      if (search + window > end) return true;
      std::int64_t first = jtty_rx_next_required_sample(h);
      if (first < base) { std::fprintf(stderr, "jttyd: audio before %lld already dropped\n", (long long)first); return false; }
      auto t0 = std::chrono::steady_clock::now();
      int r = jtty_rx_process(h, hist.data() + (first - base), int(search + window - first), first,
                              search + window, 1, o.low, o.high, o.center, o.tol);
      double dt = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
      if (r < 0) { std::fprintf(stderr, "jttyd: jtty_rx_process returned %d\n", r); return false; }
      ++steps; busy += dt; if (dt > worst) worst = dt;
      if (steps == 1) { std::printf("{\"ready\":true,\"setup_s\":%.1f}\n", dt); std::fflush(stdout); }
      if (first_step < 0) first_step = dt; else if (dt > worst_after) worst_after = dt;
      emit_updates(h);
      // 0 steps is not "need more audio" by itself: go on while the search
      // position still moves, stop only when it stands still
      if (r == 0 && jtty_rx_next_search_sample(h) == search) return true;
      // keep only what the decoder may still ask for
      std::int64_t keep = jtty_rx_next_required_sample(h);
      if (keep - base > 12000 * 30) {
        hist.erase(hist.begin(), hist.begin() + (keep - base));
        base = keep;
      }
    }
  };

  bool ok = true;
  if (from_stdin) {
    std::vector<std::int16_t> chunk(1200);           // 0.1 s
    for (;;) {
      std::size_t n = std::fread(chunk.data(), 2, chunk.size(), stdin);
      if (n == 0) break;
      hist.insert(hist.end(), chunk.begin(), chunk.begin() + n);
      if (!(ok = pump())) break;
    }
  } else {
    for (auto const& f : o.files) {
      std::vector<std::int16_t> w;
      if (!read_wav(f, w)) { ok = false; break; }
      hist.insert(hist.end(), w.begin(), w.end());
      if (!(ok = pump())) break;
    }
  }
  jtty_rx_end(h, 3);
  emit_updates(h);
  jtty_rx_destroy(h);

  if (o.stats) {
    double audio = double(base + std::int64_t(hist.size())) / 12000.0;
    double wall = std::chrono::duration<double>(std::chrono::steady_clock::now() - t_start).count();
    double step_s = double(nframe / 4) / 12000.0;
    std::fprintf(stderr, "jttyd stats: audio %.1f s, %ld steps (%.3f s audio each), "
                 "decoder %.2f s (mean %.3f s, worst %.3f s per step), wall %.2f s, "
                 "real-time factor %.2f\n",
                 audio, steps, step_s, busy, steps ? busy / steps : 0.0, worst, wall,
                 audio > 0 ? busy / audio : 0.0);
    if (steps > 1)
      std::fprintf(stderr, "jttyd stats: first step %.2f s (set-up); after it %ld steps, mean %.4f s, "
                   "worst %.4f s per step = %.0f %% of real time\n", first_step, steps - 1,
                   (busy - first_step) / double(steps - 1), worst_after,
                   100.0 * ((busy - first_step) / double(steps - 1)) / step_s);
  }
  return ok ? 0 : 1;
}

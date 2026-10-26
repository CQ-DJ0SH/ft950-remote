//! Drop-in replacement for WSJT-X's `osd174_91` (lib/ft8/osd174_91.f90),
//! the ordered-statistics decoder of the (174,91) LDPC code used by FT8/FT4.
//!
//! Port of WSJT-X code (GPL-3.0, (C) Joe Taylor K1JT, Steve Franke K9AN et al.).
//! Same algorithm, same arithmetic, same results - bit for bit:
//! * codewords are bit-packed ([u64; 3] for 174 bits), so encoding a test
//!   pattern is a few XORs instead of loops over byte arrays;
//! * every float sum is taken over the same terms in the same (ascending)
//!   order as the Fortran `sum(nxor*absrx)` - the skipped terms are exact zeros;
//! * sorting (`indexx`) and the CRC (`get_crc14`) are WSJT-X's own Fortran
//!   routines, called through FFI, so ties sort the same way.
//!
//! Only the case FT8 and FT4 use is done here: k = 91 and ndeep <= 2 (order 1,
//! with or without the first pre-processing rule). Anything else goes to the
//! original Fortran, kept as `osd174_91_f`.

const N: usize = 174;
const K: usize = 91;

type Bv = [u64; 3];

extern "C" {
    fn osd174_91_f_(llr: *const f32, k: *const i32, apmask: *const i8, ndeep: *mut i32,
                    message91: *mut i8, cw: *mut i8, nhardmin: *mut i32, dmin: *mut f32);
    fn indexx_(arr: *const f32, n: *const i32, indx: *mut i32);
    fn get_crc14_(mc: *const i8, len: *const i32, ncrc: *mut i32);
    fn encode174_91_nocrc_(message: *const i8, codeword: *mut i8);
}

#[inline(always)]
fn bit(v: &Bv, p: usize) -> bool { (v[p >> 6] >> (p & 63)) & 1 == 1 }
#[inline(always)]
fn set(v: &mut Bv, p: usize) { v[p >> 6] |= 1u64 << (p & 63); }
#[inline(always)]
fn flip(v: &mut Bv, p: usize) { v[p >> 6] ^= 1u64 << (p & 63); }
#[inline(always)]
fn xor(a: &Bv, b: &Bv) -> Bv { [a[0] ^ b[0], a[1] ^ b[1], a[2] ^ b[2]] }
#[inline(always)]
fn and(a: &Bv, b: &Bv) -> Bv { [a[0] & b[0], a[1] & b[1], a[2] & b[2]] }
#[inline(always)]
fn popcnt(a: &Bv) -> u32 { a[0].count_ones() + a[1].count_ones() + a[2].count_ones() }

/// Mask of bit positions lo..hi (0-based, hi exclusive).
fn range_mask(lo: usize, hi: usize) -> Bv {
    let mut m = [0u64; 3];
    for p in lo..hi { set(&mut m, p); }
    m
}

/// Sum of w[p] over the set bits of v, in ascending p - the order of the
/// Fortran sum, whose other terms are exact zeros.
#[inline(always)]
fn wsum(v: &Bv, w: &[f32; N]) -> f32 {
    let mut s = 0.0f32;
    for (i, &word) in v.iter().enumerate() {
        let mut x = word;
        while x != 0 {
            let t = x.trailing_zeros() as usize;
            s += w[i * 64 + t];
            x &= x - 1;
        }
    }
    s
}

/// Generator for k = 91: column j (0..N) as a 91-bit row vector, built exactly as
/// the Fortran does on its first call (unit message, CRC bits 78..k cleared).
fn gen_cols() -> &'static [u128; N] {
    use std::sync::OnceLock;
    static G: OnceLock<[u128; N]> = OnceLock::new();
    G.get_or_init(|| {
        let mut cols = [0u128; N];
        for i in 0..K {
            let mut m91 = [0i8; 91];
            m91[i] = 1;
            if i < 77 {
                let mut m96 = [0i8; 96];
                m96[..91].copy_from_slice(&m91);
                let mut ncrc: i32 = 0;
                unsafe { get_crc14_(m96.as_ptr(), &96, &mut ncrc) };
                for b in 0..14 { m91[77 + b] = ((ncrc >> (13 - b)) & 1) as i8; }
                for b in 77..K { m91[b] = 0; }   // message91(78:k)=0
            }
            let mut cw = [0i8; N];
            unsafe { encode174_91_nocrc_(m91.as_ptr(), cw.as_mut_ptr()) };
            for j in 0..N { if cw[j] == 1 { cols[j] |= 1u128 << i; } }
        }
        cols
    })
}

/// nextpat91: the next test pattern of weight iorder (mi(1..k), 1-based iflag).
fn nextpat(mi: &mut [u8; K], k: usize, iorder: usize, iflag: &mut i32) {
    let mut ind: i32 = -1;
    for i in 1..k {                       // Fortran i=1..k-1, 1-based
        if mi[i - 1] == 0 && mi[i] == 1 { ind = i as i32; }
    }
    if ind < 0 { *iflag = ind; return; }
    let ind = ind as usize;
    let mut ms = [0u8; K];
    ms[..ind - 1].copy_from_slice(&mi[..ind - 1]);
    ms[ind - 1] = 1;
    ms[ind] = 0;
    if ind + 1 < k {
        let s: usize = ms[..k].iter().map(|&x| x as usize).sum();
        let nz = iorder - s;
        for p in (k - nz)..k { ms[p] = 1; }
    }
    *mi = ms;
    for i in 0..k { if mi[i] == 1 { *iflag = (i + 1) as i32; break; } }
}

/// OSD_CHECK=1 in the environment: run the Fortran original on every call as
/// well and compare cw, nhardmin and dmin bit for bit; the totals go to stderr
/// at exit. For validation only - it costs the Fortran's time on top.
mod check {
    use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
    pub static CALLS: AtomicU64 = AtomicU64::new(0);
    pub static BAD: AtomicU64 = AtomicU64::new(0);
    pub fn on() -> bool {
        use std::sync::OnceLock;
        static ON: OnceLock<bool> = OnceLock::new();
        *ON.get_or_init(|| {
            let on = std::env::var_os("OSD_CHECK").is_some();
            if on { unsafe { libc_atexit(report) }; }
            on
        })
    }
    extern "C" { #[link_name = "atexit"] fn libc_atexit(f: extern "C" fn()) -> i32; }
    extern "C" fn report() {
        eprintln!("OSD_CHECK: {} calls, {} mismatches", CALLS.load(Relaxed), BAD.load(Relaxed));
    }
    pub fn count(bad: bool) { CALLS.fetch_add(1, Relaxed); if bad { BAD.fetch_add(1, Relaxed); } }
}

#[no_mangle]
pub unsafe extern "C" fn osd174_91_(llr: *const f32, k: *const i32, apmask: *const i8, ndeep: *mut i32,
                                   message91: *mut i8, cw_out: *mut i8, nhardmin_out: *mut i32,
                                   dmin_out: *mut f32) {
    osd_rs(llr, k, apmask, ndeep, message91, cw_out, nhardmin_out, dmin_out);
    if check::on() && *k == K as i32 && (0..=2).contains(&*ndeep) {
        let (mut m, mut c, mut nh, mut dm, mut nd) = ([0i8; 91], [0i8; N], 0i32, 0f32, *ndeep);
        osd174_91_f_(llr, k, apmask, &mut nd, m.as_mut_ptr(), c.as_mut_ptr(), &mut nh, &mut dm);
        let rc = std::slice::from_raw_parts(cw_out, N);
        let bad = rc != &c[..] || nh != *nhardmin_out || dm.to_bits() != (*dmin_out).to_bits();
        check::count(bad);
    }
}

unsafe fn osd_rs(llr: *const f32, k: *const i32, apmask: *const i8, ndeep: *mut i32,
                                   message91: *mut i8, cw_out: *mut i8, nhardmin_out: *mut i32,
                                   dmin_out: *mut f32) {
    if *k != K as i32 || *ndeep > 2 || *ndeep < 0 {
        return osd174_91_f_(llr, k, apmask, ndeep, message91, cw_out, nhardmin_out, dmin_out);
    }
    let llr = std::slice::from_raw_parts(llr, N);
    let apmask = std::slice::from_raw_parts(apmask, N);
    let gen = gen_cols();

    let mut absrx0 = [0f32; N];
    for i in 0..N { absrx0[i] = llr[i].abs(); }
    let mut indx = [0i32; N];
    indexx_(absrx0.as_ptr(), &(N as i32), indx.as_mut_ptr());

    // columns in order of decreasing reliability; genmrb as K row vectors
    let mut indices = [0usize; N];               // 0-based original positions
    let mut rows = [[0u64; 3]; K];
    for c in 0..N {
        indices[c] = (indx[N - 1 - c] - 1) as usize;
        let mut col = gen[indices[c]];
        while col != 0 {
            let r = col.trailing_zeros() as usize;
            set(&mut rows[r], c);
            col &= col - 1;
        }
    }
    // Gaussian elimination, pivots searched in columns id..k+20 (as the Fortran)
    for id in 0..K {
        for icol in id..(K + 20) {
            if bit(&rows[id], icol) {
                if icol != id {
                    for r in rows.iter_mut() {
                        if bit(r, id) != bit(r, icol) { flip(r, id); flip(r, icol); }
                    }
                    indices.swap(id, icol);
                }
                let pivot = rows[id];
                for ii in 0..K {
                    if ii != id && bit(&rows[ii], id) { rows[ii] = xor(&rows[ii], &pivot); }
                }
                break;
            }
        }
    }

    // received word in the new order
    let mut hdec: Bv = [0; 3];
    let mut absrx = [0f32; N];
    let mut apm: u128 = 0;                       // apmask of the first k positions
    for p in 0..N {
        let o = indices[p];
        if llr[o] >= 0.0 { set(&mut hdec, p); }
        absrx[p] = absrx0[o];
        if p < K && apmask[o] == 1 { apm |= 1u128 << p; }
    }
    let m0: u128 = (hdec[0] as u128) | (((hdec[1] as u128) & ((1u128 << (K - 64)) - 1)) << 64);
    let enc = |m: u128| -> Bv {
        let mut c = [0u64; 3];
        let mut x = m;
        while x != 0 {
            let r = x.trailing_zeros() as usize;
            c = xor(&c, &rows[r]);
            x &= x - 1;
        }
        c
    };
    let c0 = enc(m0);
    let nx = xor(&c0, &hdec);
    let mut nhardmin = popcnt(&nx) as i32;
    let mut dmin = wsum(&nx, &absrx);
    let mut cw = c0;

    let nd = *ndeep;
    if nd >= 1 {
        let (nord, npre1, nt, ntheta) = if nd == 1 { (1usize, 0, 40usize, 12u32) } else { (1, 1, 40, 10) };
        let par = range_mask(K, N);              // parity positions k+1..N
        let ntm = range_mask(K, K + nt);         // the first nt of them
        let pw = {                               // absrx with only the parity positions
            let mut w = [0f32; N];
            w[K..].copy_from_slice(&absrx[K..]);
            w
        };
        // e2sub lives across test patterns, as the Fortran array does: when the
        // pattern's own position is AP-masked (cycle), the neighbours are tested
        // against the previous pattern's e2sub - kept that way, bit for bit
        let mut e2sub: Bv = [0; 3];
        for iorder in 1..=nord {
            let mut misub = [0u8; K];
            for p in (K - iorder)..K { misub[p] = 1; }
            let mut iflag: i32 = (K - iorder + 1) as i32;
            while iflag >= 0 {
                let iend = if iorder == nord && npre1 == 0 { iflag } else { 1 };
                let mut msub: u128 = 0;
                for p in 0..K { if misub[p] == 1 { msub |= 1u128 << p; } }
                let csub = xor(&c0, &enc(msub));          // codeword of m0 ^ misub
                let mut d1 = 0.0f32;
                let mut n1 = iflag;
                while n1 >= iend {
                    let b = (n1 - 1) as usize;
                    let mi = msub | (1u128 << b);
                    if apm & mi != 0 { n1 -= 1; continue; }
                    let first = n1 == iflag;
                    let nd1kpt;
                    let e2;
                    if first {
                        e2sub = and(&xor(&csub, &hdec), &par);
                        e2 = e2sub;
                        nd1kpt = popcnt(&and(&e2sub, &ntm)) + 1;
                        // me ^ hdec over the first k positions is mi itself
                        let mut s = 0.0f32;
                        let mut x = mi;
                        while x != 0 {
                            let t = x.trailing_zeros() as usize;
                            s += absrx[t];
                            x &= x - 1;
                        }
                        d1 = s;
                    } else {
                        e2 = xor(&e2sub, &and(&rows[b], &par));
                        nd1kpt = popcnt(&and(&e2, &ntm)) + 2;
                    }
                    if nd1kpt <= ntheta {
                        let ce = if first { csub } else { xor(&csub, &rows[b]) };
                        let nxor = xor(&ce, &hdec);
                        let dd = if first {
                            d1 + wsum(&e2sub, &pw)
                        } else {
                            let t = if bit(&nxor, b) { absrx[b] } else { 0.0 };
                            (d1 + t) + wsum(&e2, &pw)
                        };
                        if dd < dmin {
                            dmin = dd;
                            cw = ce;
                            nhardmin = popcnt(&nxor) as i32;
                        }
                    }
                    n1 -= 1;
                }
                nextpat(&mut misub, K, iorder, &mut iflag);
            }
        }
    }

    // back to [message bits][parity bits] order
    let mut out = [0i8; N];
    for p in 0..N { if bit(&cw, p) { out[indices[p]] = 1; } }
    std::ptr::copy_nonoverlapping(out.as_ptr(), cw_out, N);
    std::ptr::copy_nonoverlapping(out.as_ptr(), message91, 91);
    let mut m96 = [0i8; 96];
    m96[..77].copy_from_slice(&out[..77]);
    m96[82..96].copy_from_slice(&out[77..91]);
    let mut nbadcrc: i32 = 0;
    get_crc14_(m96.as_ptr(), &96, &mut nbadcrc);
    if nbadcrc != 0 { nhardmin = -nhardmin; }
    *nhardmin_out = nhardmin;
    *dmin_out = dmin;
}

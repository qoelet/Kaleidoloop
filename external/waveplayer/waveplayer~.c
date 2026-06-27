#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#include "m_pd.h"
#include "waveplayer_dsp.h"

#define BUFSIZE 1024 // to read from disk, in samples

static t_class *waveplayer_tilde_class;

typedef struct _waveplayer_tilde {
    t_object  x_obj;
    t_float   x_f;              // CLASS_MAINSIGNALIN scalar fallback (speed)
    t_outlet *x_out;
    t_int x_loop_start;
    t_int x_loop_end;
    t_float x_speed;            // instantaneous speed, set per-sample
    double x_pos;
    t_int x_current_buf_num;
    int16_t x_buf[BUFSIZE];
    int16_t x_buf_last3[3];
    FILE *x_fh;
} t_waveplayer_tilde;

// get buf from file (verbatim from ancestor)
static void readbuf(t_waveplayer_tilde *x){
    if (x->x_speed >= 0) {
        x->x_buf_last3[0] = x->x_buf[BUFSIZE - 3];
        x->x_buf_last3[1] = x->x_buf[BUFSIZE - 2];
        x->x_buf_last3[2] = x->x_buf[BUFSIZE - 1];
    } else {
        x->x_buf_last3[0] = x->x_buf[0];
        x->x_buf_last3[1] = x->x_buf[1];
        x->x_buf_last3[2] = x->x_buf[2];
    }
    if (x->x_fh != NULL) {
        fseek(x->x_fh, x->x_current_buf_num * BUFSIZE * 2, SEEK_SET);
        fread(x->x_buf, 2, BUFSIZE, x->x_fh);
    } else {
        memset(x->x_buf, 0, BUFSIZE * 2);
        memset(x->x_buf_last3, 0, 6);
    }
}

static void openfile(t_waveplayer_tilde *x, const char *fn){
    if (x->x_fh != NULL) fclose(x->x_fh);
    post("opening %s", fn);
    x->x_fh = fopen(fn,"r");
    if( x->x_fh == NULL) {
        pd_error(x, "Unable to open file %s", fn);
        return;
    }
    uint32_t len;
    fseek(x->x_fh, 0, SEEK_END);
    len = ftell(x->x_fh);
    post("loaded file len: %d samples", (len - 44) / 2);
    post("that is: %f secs", (float)(len - 44) / 2 / 44100);
    x->x_loop_end = (len / 2);
    x->x_loop_start = 44;
    x->x_pos = x->x_loop_start + 1;
    x->x_current_buf_num = -1;
}

static t_int *waveplayer_tilde_perform(t_int *w)
{
    t_waveplayer_tilde *x = (t_waveplayer_tilde *)(w[1]);
    t_sample *in  = (t_sample *)(w[2]);   // speed signal
    t_sample *out = (t_sample *)(w[3]);
    int n = (int)(w[4]);
    int i, bufnum, bufi;

    for (i = 0; i < n; i++){
        t_float speed = in[i];
        if (speed > 3) speed = 3;
        if (speed < -3) speed = -3;
        x->x_speed = speed;

        x->x_pos += x->x_speed;

        if (x->x_speed >= 0) {
            if (x->x_pos >= (x->x_loop_end - 2)) x->x_pos = x->x_loop_start + 1;
        } else {
            if (x->x_pos <= (x->x_loop_start + 1)) x->x_pos = x->x_loop_end - 2;
        }

        if (x->x_speed >= 0) bufnum = ((uint32_t)x->x_pos + 2) / BUFSIZE;
        else bufnum = ((uint32_t)x->x_pos - 1) / BUFSIZE;
        bufi = (uint32_t)x->x_pos % BUFSIZE;

        if (bufnum != x->x_current_buf_num) {
            x->x_current_buf_num = bufnum;
            readbuf(x);
        }

        t_sample frac, a, b, c, d;
        frac = x->x_pos - (uint32_t)x->x_pos;

        if (x->x_speed >= 0) {
            if (bufi == BUFSIZE - 2) {
                a = x->x_buf_last3[0]; b = x->x_buf_last3[1];
                c = x->x_buf_last3[2]; d = x->x_buf[0];
            } else if (bufi == BUFSIZE - 1) {
                a = x->x_buf_last3[1]; b = x->x_buf_last3[2];
                c = x->x_buf[0];       d = x->x_buf[1];
            } else if (bufi == 0) {
                a = x->x_buf_last3[2]; b = x->x_buf[0];
                c = x->x_buf[1];       d = x->x_buf[2];
            } else {
                a = x->x_buf[bufi-1]; b = x->x_buf[bufi];
                c = x->x_buf[bufi+1]; d = x->x_buf[bufi+2];
            }
        } else {
            if (bufi == 0) {
                a = x->x_buf[BUFSIZE - 1]; b = x->x_buf_last3[0];
                c = x->x_buf_last3[1];     d = x->x_buf_last3[2];
            } else if (bufi == BUFSIZE - 1) {
                a = x->x_buf[BUFSIZE - 2]; b = x->x_buf[BUFSIZE - 1];
                c = x->x_buf_last3[0];     d = x->x_buf_last3[1];
            } else if (bufi == BUFSIZE - 2) {
                a = x->x_buf[BUFSIZE - 3]; b = x->x_buf[BUFSIZE - 2];
                c = x->x_buf[BUFSIZE - 1]; d = x->x_buf_last3[0];
            } else {
                a = x->x_buf[bufi-1]; b = x->x_buf[bufi];
                c = x->x_buf[bufi+1]; d = x->x_buf[bufi+2];
            }
        }

        a /= 32768; b /= 32768; c /= 32768; d /= 32768;
        *out++ = wp_interp4(a, b, c, d, frac);
    }
    return (w + 5);
}

static void waveplayer_tilde_dsp(t_waveplayer_tilde *x, t_signal **sp)
{
    dsp_add(waveplayer_tilde_perform, 4, x,
            sp[0]->s_vec, sp[1]->s_vec, (t_int)sp[0]->s_n);
}

static void waveplayer_tilde_free(t_waveplayer_tilde *x)
{
    outlet_free(x->x_out);
    if (x->x_fh != NULL) fclose(x->x_fh);
}

static void waveplayer_open(t_waveplayer_tilde *x, t_symbol *s,
                            int argc, t_atom *argv)
{
    (void)s;
    t_symbol *filesym = atom_getsymbolarg(0, argc, argv);
    openfile(x, filesym->s_name);
}

static void *waveplayer_tilde_new(void)
{
    t_waveplayer_tilde *x = (t_waveplayer_tilde *)pd_new(waveplayer_tilde_class);
    x->x_out = outlet_new(&x->x_obj, &s_signal);
    x->x_f = 1;
    x->x_loop_start = 44;
    x->x_loop_end = 44100;
    x->x_pos = x->x_loop_start + 1;
    x->x_current_buf_num = -1;
    x->x_speed = 1;
    x->x_fh = NULL;
    x->x_buf_last3[0] = x->x_buf_last3[1] = x->x_buf_last3[2] = 0;
    return (void *)x;
}

void waveplayer_tilde_setup(void) {
    waveplayer_tilde_class = class_new(gensym("waveplayer~"),
        (t_newmethod)waveplayer_tilde_new,
        (t_method)waveplayer_tilde_free,
        sizeof(t_waveplayer_tilde),
        CLASS_DEFAULT, 0);
    CLASS_MAINSIGNALIN(waveplayer_tilde_class, t_waveplayer_tilde, x_f);
    class_addmethod(waveplayer_tilde_class, (t_method)waveplayer_tilde_dsp,
        gensym("dsp"), A_CANT, 0);
    class_addmethod(waveplayer_tilde_class, (t_method)waveplayer_open,
        gensym("open"), A_GIMME, 0);
}

VARYING vec3 localPos;
VARYING vec3 localNormal;
VARYING vec2 coord;
void MAIN() {
    vec3 v=normalize(vec3(0,0,650)-localPos);
    float fres=pow(1.0-abs(dot(normalize(localNormal),v)),2.1);
    vec3 bary=vec3(coord,1.0-coord.x-coord.y);
    vec3 width=fwidth(bary)*1.1;
    vec3 aa=smoothstep(vec3(0),width,bary);
    float edge=1.0-min(aa.x,min(aa.y,aa.z));
    float folds=0.5+0.5*sin(localPos.y*0.09+localPos.x*0.05+time*0.4);
    float light=(0.018+fres*0.46+edge*0.08)*(0.6+folds*0.4);
    float alive=0.025+assembly*(0.85+speech*0.25);
    FRAGCOLOR=vec4(tint*light*alive*brightness,1.0);
}
